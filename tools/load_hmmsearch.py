import re
from collections import defaultdict

#BioPython
from Bio import SearchIO
from Bio import SeqIO
from Bio.Seq import Seq

#Django libraires
from browse.models import *
from djangophylocore.models import Taxonomy
from django.db.models import Max

#Custom librairies
from tools.hist_ss import get_hist_ss
from tools.taxonomy_from_gis import taxonomy_from_gis

def get_sequence(gi, sequence_file):
  """sequences is likely large, so we don't want to store it in memory.
  Read file in each time, saving only sequence with gi"""
  for s in SeqIO.parse(sequence_file, "fasta"):
    if gi in s.id:
      return s

species_re = re.compile(r'\[(.*?)\]')
def taxonomy_from_header(header, gi):
  match = species_re.findall(header)
  if match:
    organism = match[-1]
  else:
    print "No match for {}: {}, searh NCBI".format(gi, header)
    while True:
      try:
        organism = taxonomy_from_gis([gi]).next()
        break
      except StopIteration:
        pass
  organism = organism.replace(":", " ")
  organism = re.sub(r'([a-zA-Z0-9]+)[\./#_:]([a-zA-Z0-9]+)', r'\1 \2', organism)
  organism = re.sub(r'([a-zA-Z0-9]+)\. ', r'\1 ', organism)
  organism = re.sub(r"['\(\)\.]", r'', organism)
  organism = organism.lower()
  try:
    return Taxonomy.objects.get(name=organism.lower())
  except:
    try:
      genus = Taxonomy.objects.get(name=organism.split(" ")[0].lower())
      if genus.type_name != "scientific name":
        genus = genus.get_scientific_names()[0]
    except:
      print header
      return Taxonomy.objects.get(name="unidentified")

    #Maybe the var is wrong
    if "var" in organism:
      try:
        org_end = organism.split("var ")[1]
      except IndexError:
        return Taxonomy.objects.get(name="unidentified")
      best_orgs = genus.children.filter(name__endswith=org_end).all()
      if len(best_orgs):
        return best_orgs[0]
      else:
        return Taxonomy.objects.get(name="unidentified")
    else:
      return Taxonomy.objects.get(name="unidentified")
      print organism
      print "Genus =", genus
      print "Are you sure there is no taxa that matches? Check again:"
      taxas = []
      for i, c in enumerate(genus.children.all()):
        print "{}: {} ==?== {}".format(i, c, organism)
        taxas.append(c)
      index = raw_input("Choose the index that best matches: ")

      try:
        index = int(save)
      except ValueError:
        return Taxonomy.objects.get(name="unidentified")

      try:
        return taxas[index]
      except IndexError:
        return Taxonomy.objects.get(name="unidentified")


def load_variants(hmmerFile, sequences, reset=True):
  """Save domain hits from a hmmer hmmsearch file into the Panchenko Histone
  Variant DB format.

  Parameters:
  ___________
  hmmerFile : string
    Path to HMMer hmmsearch output file
  sequences : string
    Path to of sequences used to search the HMM
  threshold : float
    Keep HSPS with scores >= threshold. Optional.
  """
  if reset:
    Sequence.objects.all().delete()
    Features.objects.all().delete()
    Score.objects.all().delete()

  unknown_model = Variant.objects.get(id="Unknown")
  
  for variant_query in SearchIO.parse(hmmerFile, "hmmer3-text"):
    print "Loading variant:", variant_query.id
    try:
      variant_model = Variant.objects.get(id=variant_query.id)
    except:
      if "H2A" in variant_query.id:
        core_histone = Histone.objects.get(id="H2A")
      elif "H2B" in variant_query.id:
        core_histone = Histone.objects.get(id="H2B")
      elif "H3" in variant_query.id:
        core_histone = Histone.objects.get(id="H3")
      elif "H4" in variant_query.id:
        core_histone = Histone.objects.get(id="H4")
      elif "H1" in variant_query.id:
        core_histone = Histone.objects.get(id="H1")
      else:
        continue
      variant_model = Variant(id=variant_query.id, core_type=core_histone)
      variant_model.save()
    for hit in variant_query:
      headers = "{}{}".format(hit.id, hit.description).split("gi|")[1:]
      for header in headers:
        gi = header.split("|")[0]
        for i, hsp in enumerate(hit):
          seqs = Sequence.objects.filter(id=gi).annotate(score=Max("scores__score"))
          if len(seqs) > 0: 
            #Sequence already exists. Compare bit scores, if current bit score is 
            #greater than current, reassign variant and update scores. Else, append score
            seq = seqs.first()
            if (hsp.bitscore > seq.score) and hsp.bitscore>=variant_model.hmmthreshold:
              #best scoring
              seq.variant = variant_model
              seq.sequence = str(hsp.hit.seq)
              seq.save()
              update_features(seq)
              print "UPDATED VARIANT"
          else:
            taxonomy = taxonomy_from_header(header, gi)
            sequence = Seq(str(hsp.hit.seq))
            seq = add_sequence(
              gi,  
              variant_model if hsp.bitscore >= variant_model.hmmthreshold else unknown_model, 
              taxonomy, 
              header, 
              sequence)
          print seq
          #Add a score even if it is a below threshold
          add_score(seq, variant_model, hsp)

def load_cores(hmmerFile, reset=True):
  unknown_model = Variant.objects.get(id="Unknown")
  score_num = 0

  if reset:
    variants = Variant.objects.filter(id__contains="cononical")
    for variant in variants:
      for sequence in variant.sequences:
        sequence.features.delete()
        sequence.scores.delete()
      variant.sequences.delete()
    variants.delete()

  for core_query in SearchIO.parse(hmmerFile, "hmmer3-text"):
    print "Loading core:", core_query.id
    try:
      core_histone = Histone.objects.get(id=core_query.id)
    except:
      continue

    try:
      canonical_model = Variant.objects.get(id="canonical{}".format(core_query.id))
    except:
      canonical_model = Variant(id="canonical{}".format(core_query.id), core_type=core_histone)
      canonical_model.save()

    for hit in core_query:
      headers = "{}{}".format(hit.id, hit.description).split("gi|")[1:]
      for header in headers:
        gi = header.split("|")[0]
        for i, hsp in enumerate(hit):
          seqs = Sequence.objects.filter(id=gi).annotate(score=Max("scores__score"))
          if len(seqs) > 0: 
            #Sequence already exists. Compare bit scores, if current bit score is 
            #greater than current, reassign variant and update scores. Else, append score
            seq = seqs.first()
            if hsp.bitscore >= canonical_model.hmmthreshold and \
                (seq.variant.id == "Unknown" or  \
                  ("canonical" in seq.variant.id and hsp.bitscore > seq.score)) :
              seq.variant = canonical_model
              seq.sequence = str(hsp.hit.seq)
              seq.save()
              update_features(seq)
              print "UPDATED VARIANT"
          else:
            #only add sequence if it was not found by the variant models
            taxonomy = taxonomy_from_header(header, gi)
            sequence = Seq(str(hsp.hit.seq))
            seq = add_sequence(
              gi,  
              canonical_model if hsp.bitscore >= canonical_model.hmmthreshold else unknown_model, 
              taxonomy, 
              header, 
              sequence)
          print seq
          add_score(seq, canonical_model, hsp)

def add_sequence(gi, variant_model, taxonomy, header, sequence):
  if not variant_model.core_type.id == "H1" and not variant_model.core_type.id == "Unknown":
    hist_identified, ss_position, sequence = get_hist_ss(sequence, variant_model.core_type.id, save_alignment=True)
    sequence = str(sequence.seq)
  else:
    ss_position = defaultdict(lambda: (None, None))
    sequence = str(sequence)
  seq = Sequence(
    id       = gi,
    variant  = variant_model,
    gene     = None,
    splice   = None,
    taxonomy = taxonomy,
    header   = header,
    sequence = sequence,
    reviewed = False,
    )
  seq.save()
  return seq

def update_features(seq,ss_position=None, variant_model=None):
  if ss_position is None and variant_model is not None:
    hist_identified, ss_position, sequence = get_hist_ss(sequence, variant_model.core_type.id, save_alignment=True)
    sequence = str(sequence.seq)
  if ss_position is None: return

  if seq.features:
    seq.features.delete()

  if not variant_model.core_type.id == "H1":
    features = Features(
      sequence             = seq,
      alphaN_start         = ss_position["alphaN"][0],
      alphaN_end           = ss_position["alphaN"][1],
      alpha1_start         = ss_position["alpha1"][0],
      alpha1_end           = ss_position["alpha1"][1],
      alpha1ext_start      = ss_position["alpha1ext"][0],
      alpha1ext_end        = ss_position["alpha1ext"][1],
      alpha2_start         = ss_position["alpha2"][0],
      alpha2_end           = ss_position["alpha2"][1],
      alpha3_start         = ss_position["alpha3"][0],
      alpha3_end           = ss_position["alpha3"][1],
      alpha3ext_start      = ss_position["alpha3ext"][0],
      alpha3ext_end        = ss_position["alpha3ext"][1],
      alphaC_start         = ss_position["alphaC"][0],
      alphaC_end           = ss_position["alphaC"][1],
      beta1_start          = ss_position["beta1"][0],
      beta1_end            = ss_position["beta1"][1],
      beta2_start          = ss_position["beta2"][0],
      beta2_end            = ss_position["beta2"][1],
      loopL1_start         = ss_position["loopL1"][0],
      loopL1_end           = ss_position["loopL1"][1],
      loopL2_start         = ss_position["loopL2"][0],
      loopL2_end           = ss_position["loopL2"][1],
      mgarg1_start         = ss_position["mgarg1"][0],
      mgarg1_end           = ss_position["mgarg1"][1],
      mgarg2_start         = ss_position["mgarg2"][0],
      mgarg2_end           = ss_position["mgarg2"][1],
      mgarg3_start         = ss_position["mgarg3"][0],
      mgarg3_end           = ss_position["mgarg3"][1],
      docking_domain_start = ss_position["docking domain"][0],
      docking_domain_end   = ss_position["docking domain"][1],
      core                 = ss_position["core"],
      )
    features.save()


def add_score(seq, variant_model, hsp):
  score_num = Score.objects.count()+1
  score = Score(
    id              = score_num,
    sequence        = seq,
    variant         = variant_model,
    score           = hsp.bitscore,
    evalue          = hsp.evalue,
    above_threshold = hsp.bitscore >= variant_model.hmmthreshold,
    hmmStart        = hsp.query_start,
    hmmEnd          = hsp.query_end,
    seqStart        = hsp.hit_start,
    seqEnd          = hsp.hit_end
    )
  score.save()
  
