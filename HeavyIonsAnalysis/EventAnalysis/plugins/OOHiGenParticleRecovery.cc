// Recover the stable HIJING particles for the signal-only central OO MiniAOD.
// The event table is made by build_oo_hijing_truth.py from the original GEN-SIM.
#include "DataFormats/HepMCCandidate/interface/GenParticle.h"
#include "DataFormats/PatCandidates/interface/PackedGenParticle.h"
#include "DataFormats/Provenance/interface/EventID.h"
#include "FWCore/Framework/interface/Event.h"
#include "FWCore/Framework/interface/MakerMacros.h"
#include "FWCore/Framework/interface/global/EDProducer.h"
#include "FWCore/ParameterSet/interface/ConfigurationDescriptions.h"
#include "FWCore/ParameterSet/interface/ParameterSet.h"
#include "FWCore/ParameterSet/interface/ParameterSetDescription.h"
#include "FWCore/Utilities/interface/Exception.h"
#include "FWCore/Utilities/interface/InputTag.h"
#include "SimDataFormats/PileupSummaryInfo/interface/PileupSummaryInfo.h"

#include <cmath>
#include <cstdint>
#include <fstream>
#include <limits>
#include <map>
#include <memory>
#include <sstream>
#include <string>
#include <vector>

namespace {
  struct Particle {
    double pt, eta, phi, mass;
    int charge, pdgId;
  };
  using EventTable = std::map<edm::EventID, std::vector<Particle>>;

  EventTable readTable(std::string const& path) {
    std::ifstream file(path);
    std::string line;
    if (!std::getline(file, line) || line != "OOHIJING_V1")
      throw cms::Exception("OOHiGenParticleRecovery") << "Cannot read an OOHIJING_V1 table from " << path;

    EventTable events;
    while (std::getline(file, line)) {
      std::istringstream header(line);
      char tag;
      int64_t run, lumi, event, count;
      std::string lfn, extra;
      if (!(header >> tag >> run >> lumi >> event >> count >> lfn) || tag != 'E' || header >> extra ||
          run <= 0 || run > std::numeric_limits<edm::RunNumber_t>::max() ||
          lumi <= 0 || lumi > std::numeric_limits<edm::LuminosityBlockNumber_t>::max() ||
          event <= 0 || count < 0 || lfn.rfind("/store/", 0) != 0)
        throw cms::Exception("OOHiGenParticleRecovery") << "Invalid event header in " << path << ": " << line;

      edm::EventID key(run, lumi, event);
      auto [it, inserted] = events.try_emplace(key);
      if (!inserted)
        throw cms::Exception("OOHiGenParticleRecovery") << "Repeated HIJING event " << key << " in " << path;
      for (int64_t i = 0; i < count; ++i) {
        Particle p;
        if (!std::getline(file, line))
          throw cms::Exception("OOHiGenParticleRecovery") << "Incomplete HIJING event " << key << " in " << path;
        std::istringstream row(line);
        if (!(row >> tag >> p.pt >> p.eta >> p.phi >> p.mass >> p.charge >> p.pdgId) ||
            tag != 'P' || row >> extra || !std::isfinite(p.pt) || !std::isfinite(p.eta) || !std::isfinite(p.phi) ||
            !std::isfinite(p.mass) || p.pt < 0)
          throw cms::Exception("OOHiGenParticleRecovery") << "Invalid HIJING particle for " << key << " in " << path;
        it->second.push_back(p);
      }
    }
    if (file.bad())
      throw cms::Exception("OOHiGenParticleRecovery") << "Error reading " << path;
    return events;
  }

  edm::EventID backgroundId(std::vector<PileupSummaryInfo> const& summaries, edm::EventID const& primary) {
    edm::EventID const* id = nullptr;
    for (auto const& bx : summaries) {
      if (bx.getBunchCrossing() != 0)
        continue;
      if (id != nullptr || bx.getPU_NumInteractions() != 1 || bx.getPU_EventID().size() != 1)
        throw cms::Exception("OOHiGenParticleRecovery") << "Expected one in-time HIJING interaction in " << primary;
      id = &bx.getPU_EventID().front();
    }
    if (id == nullptr)
      throw cms::Exception("OOHiGenParticleRecovery") << "No in-time pileup summary in " << primary;
    return *id;
  }
}  // namespace

class OOHiGenParticleRecovery : public edm::global::EDProducer<> {
public:
  explicit OOHiGenParticleRecovery(edm::ParameterSet const& config)
      : packed_(consumes<pat::PackedGenParticleCollection>(config.getParameter<edm::InputTag>("src"))),
        signal_(consumes<pat::PackedGenParticleRefVector>(config.getParameter<edm::InputTag>("signal"))),
        pileup_(consumes<std::vector<PileupSummaryInfo>>(config.getParameter<edm::InputTag>("pileup"))),
        backgrounds_(readTable(config.getParameter<std::string>("backgroundFile"))) {
    produces<pat::PackedGenParticleCollection>();
    produces<pat::PackedGenParticleRefVector>("signal");
  }

  static void fillDescriptions(edm::ConfigurationDescriptions& descriptions) {
    edm::ParameterSetDescription desc;
    desc.add<edm::InputTag>("src", edm::InputTag("packedGenParticles"));
    desc.add<edm::InputTag>("signal", edm::InputTag("packedGenParticlesSignal"));
    desc.add<edm::InputTag>("pileup", edm::InputTag("slimmedAddPileupInfo"));
    desc.add<std::string>("backgroundFile");
    descriptions.addWithDefaultLabel(desc);
  }

  void produce(edm::StreamID, edm::Event& event, edm::EventSetup const&) const override {
    auto const packed = event.getHandle(packed_);
    auto const& refs = event.get(signal_);
    if (!packed.isValid() || refs.size() != packed->size())
      throw cms::Exception("OOHiGenParticleRecovery") << "Expected signal-only packedGenParticles in " << event.id();
    std::vector<bool> seen(packed->size(), false);
    for (auto const& ref : refs) {
      if (ref.isNull() || ref.id() != packed.id() || ref.key() >= seen.size() || seen[ref.key()])
        throw cms::Exception("OOHiGenParticleRecovery") << "Invalid or repeated signal ref in " << event.id();
      seen[ref.key()] = true;
    }

    auto key = backgroundId(event.get(pileup_), event.id());
    auto found = backgrounds_.find(key);
    if (found == backgrounds_.end())
      throw cms::Exception("OOHiGenParticleRecovery") << "Missing HIJING event " << key << " for " << event.id();
    auto mixed = std::make_unique<pat::PackedGenParticleCollection>();
    mixed->reserve(packed->size() + found->second.size());
    mixed->insert(mixed->end(), packed->begin(), packed->end());
    for (auto const& p : found->second) {
      reco::GenParticle particle(p.charge, reco::Candidate::PolarLorentzVector(p.pt, p.eta, p.phi, p.mass),
                                 reco::Candidate::Point(0., 0., 0.), p.pdgId, 1, true);
      // Apply the same quantisation as PAT. Only kinematics and binary origin are reconstructed.
      mixed->emplace_back(particle, reco::GenParticleRef());
    }

    auto const output = event.put(std::move(mixed));
    auto signal = std::make_unique<pat::PackedGenParticleRefVector>();
    for (auto const& ref : refs)
      signal->push_back(pat::PackedGenParticleRef(output, ref.key()));
    event.put(std::move(signal), "signal");
  }

private:
  edm::EDGetTokenT<pat::PackedGenParticleCollection> const packed_;
  edm::EDGetTokenT<pat::PackedGenParticleRefVector> const signal_;
  edm::EDGetTokenT<std::vector<PileupSummaryInfo>> const pileup_;
  EventTable const backgrounds_;
};

DEFINE_FWK_MODULE(OOHiGenParticleRecovery);
