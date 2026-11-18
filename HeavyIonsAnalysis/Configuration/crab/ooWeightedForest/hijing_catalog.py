#!/usr/bin/env python3
"""Index the original OO HIJING event headers once and resolve background truth for a forest job."""

import argparse
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor
import gzip
import hashlib
import json
import multiprocessing
import os
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile

# SCRAM checkout on the submit host; CRAB additionally ships the pilot helper next to this file.
if not Path(__file__).with_name("build_oo_hijing_truth.py").is_file():
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "test"))
from build_oo_hijing_truth import HIJING_DATASET, entry_id, open_events, packed_input_rows


def das(query, as_json=False):
    command = ["dasgoclient", "-query", query, "-limit", "0"]
    if as_json:
        command.append("-json")
    result = subprocess.run(command, check=True, capture_output=True, text=True, timeout=300)
    return json.loads(result.stdout) if as_json else result.stdout.splitlines()


def dataset_manifest():
    rows = das("summary dataset=" + HIJING_DATASET, as_json=True)
    summaries = [value for row in rows for value in row["summary"]]
    if len(summaries) != 1:
        raise ValueError("Expected one DBS dataset summary for " + HIJING_DATASET)
    files = {}
    for row in das("file dataset=" + HIJING_DATASET, as_json=True):
        for record in row["file"]:
            lfn = record["name"]
            if not re.fullmatch(r"/store/[^\s]+\.root", lfn):
                raise ValueError(f"Unexpected filename in the DBS listing: {lfn!r}")
            count = int(record["nevents"]) if "nevents" in record else None
            if lfn in files and count != files[lfn]:
                raise ValueError(f"Conflicting DBS event counts for {lfn}")
            files[lfn] = count
    if not files or len(files) != int(summaries[0]["nfiles"]):
        raise ValueError(f"Listed {len(files)} files; DBS summary reports {summaries[0]['nfiles']}")
    return dict(sorted(files.items())), int(summaries[0]["nevents"])


def index_file(task):
    lfn, cache = task
    checkpoint = cache / (hashlib.sha256(lfn.encode()).hexdigest() + ".json.gz")
    if not checkpoint.exists():
        with open_events(lfn) as events:
            keys = [entry_id(events, i, lfn) for i in range(events.size())]
        with tempfile.NamedTemporaryFile(dir=cache, delete=False) as tmp:
            name = Path(tmp.name)
        try:
            with gzip.open(name, "wt", encoding="ascii") as out:
                json.dump([lfn, keys], out, separators=(",", ":"))
            os.replace(name, checkpoint)
        finally:
            name.unlink(missing_ok=True)
    return checkpoint


def read_checkpoint(path, lfn, expected):
    with gzip.open(path, "rt", encoding="ascii") as stream:
        source, keys = json.load(stream)
    if source != lfn:
        raise ValueError(f"Checkpoint {path} describes a different file")
    if expected is not None and len(keys) != expected:
        raise ValueError(f"Read {len(keys)} headers from {lfn}; DBS reports {expected}")
    for entry, key in enumerate(keys):
        if len(key) != 3 or any(type(n) is not int or n < 1 for n in key):
            raise ValueError(f"Invalid EventID at entry {entry} of {lfn}")
        yield (*key, entry)


def assemble(database, files, checkpoints, expected):
    with sqlite3.connect(database) as db:
        db.executescript("""
            PRAGMA user_version = 1;
            CREATE TABLE source(id INTEGER PRIMARY KEY, lfn TEXT NOT NULL UNIQUE);
            CREATE TABLE event(run INTEGER, lumi INTEGER, event INTEGER, source INTEGER NOT NULL,
                               entry INTEGER NOT NULL, PRIMARY KEY(run,lumi,event)) WITHOUT ROWID;
            CREATE TABLE manifest(dataset TEXT NOT NULL, events INTEGER NOT NULL, files INTEGER NOT NULL);
        """)
        for i, (lfn, checkpoint) in enumerate(zip(files, checkpoints), 1):
            db.execute("INSERT INTO source VALUES(?,?)", (i, lfn))
            rows = ((*key, i, entry) for *key, entry in read_checkpoint(checkpoint, lfn, files[lfn]))
            try:
                db.executemany("INSERT INTO event VALUES(?,?,?,?,?)", rows)
            except sqlite3.IntegrityError as exc:
                raise ValueError(f"Duplicate HIJING EventID while indexing {lfn}. "
                                 "The MiniAOD BX0 EventID by itself is ambiguous.") from exc
            if i % 50 == 0:
                print(f"Indexed {i}/{len(files)} HIJING files", flush=True)
        n_events = db.execute("SELECT count(*) FROM event").fetchone()[0]
        if n_events != expected or n_events == 0:
            raise ValueError(f"Indexed {n_events} events; expected {expected} from DBS")
        db.execute("INSERT INTO manifest VALUES(?,?,?)", (HIJING_DATASET, n_events, len(files)))


def build(output, cache, workers):
    if output.exists():
        raise FileExistsError(output)
    files, expected = dataset_manifest()
    cache.mkdir(parents=True, exist_ok=True)
    print(f"Indexing {len(files)} valid files, {expected} events", flush=True)
    with tempfile.TemporaryDirectory() as work:
        database = Path(work) / "catalog.sqlite"
        with ProcessPoolExecutor(workers) as pool:
            checks = pool.map(index_file, ((lfn, cache) for lfn in files))
            assemble(database, files, checks, expected)
        with tempfile.NamedTemporaryFile(dir=output.parent, delete=False) as tmp:
            name = Path(tmp.name)
        try:
            with database.open("rb") as src, gzip.open(name, "wb", compresslevel=6) as dst:
                shutil.copyfileobj(src, dst)
            os.link(name, output)
        finally:
            name.unlink(missing_ok=True)
    print(f"Wrote {output} ({output.stat().st_size / 1e6:.1f} MB)")


def locations(catalog, wanted):
    by_file = defaultdict(list)
    with tempfile.TemporaryDirectory() as work:
        database = Path(work) / "catalog.sqlite"
        with gzip.open(catalog, "rb") as src, database.open("wb") as dst:
            shutil.copyfileobj(src, dst)
        with sqlite3.connect(database) as db:
            if db.execute("PRAGMA user_version").fetchone()[0] != 1:
                raise ValueError("Unsupported HIJING catalog version")
            manifest = db.execute("SELECT dataset,events,files FROM manifest").fetchall()
            counts = db.execute("SELECT (SELECT count(*) FROM event), (SELECT count(*) FROM source)").fetchone()
            if manifest != [(HIJING_DATASET, *counts)] or min(counts) < 1:
                raise ValueError("Incomplete HIJING catalog or incorrect dataset")
            for key in sorted(wanted):
                row = db.execute("SELECT source.lfn,event.entry FROM event JOIN source ON source.id=event.source "
                                 "WHERE event.run=? AND event.lumi=? AND event.event=?", key).fetchone()
                if row is None:
                    raise ValueError(f"HIJING EventID {key} absent from the catalog")
                by_file[row[0]].append((row[1], key))
    return by_file


def write_source(stream, lfn, indexed):
    from DataFormats.FWLite import Handle

    handle = Handle("std::vector<reco::GenParticle>")
    with open_events(lfn) as events:
        for entry, key in sorted(indexed):
            if entry_id(events, entry, lfn) != key:
                raise ValueError(f"Wrong EventID for {key} in {lfn} entry {entry}")
            rows = packed_input_rows(events, handle, key)
            print("E", *key, len(rows), lfn, file=stream)
            for row in rows:
                print("P", *row, file=stream)


def write_part(task):
    lfn, indexed, path = task
    with path.open("w", encoding="ascii") as part:
        write_source(part, lfn, indexed)
    return path


def write_selected(wanted, catalog, output, workers=1):
    """Write an OOHIJING_V1 particle table and return the number of remote files opened."""
    if workers < 1:
        raise ValueError("HIJING reader count must be positive")
    by_file = locations(catalog, wanted)
    output = Path(output)
    # A failed remote read must not publish a partial table.
    with tempfile.TemporaryDirectory(dir=output.parent) as work:
        work = Path(work)
        table = work / "table"
        with table.open("w", encoding="ascii") as tmp:
            tmp.write("OOHIJING_V1\n")
            sources = sorted(by_file.items())
            if workers == 1 or len(sources) < 2:
                for lfn, indexed in sources:
                    write_source(tmp, lfn, indexed)
            else:
                tasks = ((lfn, indexed, work / str(i)) for i, (lfn, indexed) in enumerate(sources))
                # Each FWLite reader owns a separate ROOT runtime. Never fork the active MiniAOD reader.
                with ProcessPoolExecutor(min(workers, len(sources)),
                                         mp_context=multiprocessing.get_context("spawn")) as pool:
                    for path in pool.map(write_part, tasks):
                        with path.open(encoding="ascii") as part:
                            shutil.copyfileobj(part, tmp)
                        path.unlink()
        os.link(table, output)
    print(f"Resolved {len(wanted)} distinct HIJING events from {len(by_file)} GEN-SIM files", flush=True)
    return len(by_file)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path, help="new .sqlite.gz file to ship with CRAB")
    parser.add_argument("--work", type=Path, default=Path("hijing-header-cache"), help="resumable per-file checkpoints")
    parser.add_argument("--workers", type=int, default=4, help="concurrent remote header scans")
    args = parser.parse_args()
    if args.workers < 1:
        parser.error("--workers must be positive")
    build(args.output, args.work, args.workers)
