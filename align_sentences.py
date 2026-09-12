#!/usr/bin/env python3

import numpy as np
import torch
import re
from sklearn.metrics.pairwise import cosine_similarity


from functools import cache ###lazy imports
@cache
def get_model():
    """
    this function inits the transformers, can take about 30 seconds, or more if no weights are cached.
    """
    from sentence_transformers import SentenceTransformer

    print("initting transformers...")
    model = SentenceTransformer(
        "paraphrase-multilingual-MiniLM-L12-v2"
    )
    print("....done.")
    return model

def align( src_text, tgt_text ):

    model = get_model() ##slow only on first call.

    matches = list(re.finditer(r'\S+', src_text))
    src_words = [m.group() for m in matches]
    src_offsets = [(m.start(), m.end()) for m in matches]

    matches = list(re.finditer(r'\S+', tgt_text))
    tgt_words = [m.group() for m in matches]
    tgt_offsets = [(m.start(), m.end()) for m in matches]

    src_emb = model.encode(src_words)
    tgt_emb = model.encode(tgt_words)

    ##retruns a matrix NxN of word matches.
    sim = np.array( cosine_similarity(tgt_emb, src_emb) )

    ##need a list of each target word for which the best
    ##match is the source word, or failing that the best match for the source word.
    ##
    ## need to find the single target word with the best match, then join it to any contiguous words which are also good    
    alignments = {}
    for soff in src_offsets: alignments[soff] = []
    for i, toff in enumerate(tgt_offsets):
        best_j   = np.argmax(sim[i,:])       #best source for this target
        best_src = src_offsets[ best_j ]
        alignments[best_src].append( (toff, sim[i, best_j]) )
    for j, soff in enumerate(src_offsets):
        if len(alignments[soff]) == 0:
            best_i   = np.argmax(sim[:,j])
            best_tgt = tgt_offsets[ best_i ] #if no target for a source, find best target
            alignments[soff].append( (best_tgt, sim[best_i,j]) )

    ##sort by quality descending
    for s in alignments:
        if len(alignments[s]) < 2 : continue
        alignments[s].sort(key=lambda x: x[1], reverse=True)
         

    return alignments


if __name__ == "__main__":

    source = (
        "Luxembourg is considered quite a good democracy by global measures of democracy quality."
    )

    target = (
        "Le Luxembourg est considéré comme une bonne démocratie selon des mesures mondiales de la qualité des démocraties."
    )

    print()
    print("ALIGNMENTS")
    print("=" * 60)
    alignment = align( source, target )
    for src in alignment:
#        print(f"{src:15s} -> {tgt:15s} ({score:.3f})")
        print(src, source[src[0]:src[1]], " ---> ", alignment[src]  )
        for a, q in alignment[src]: 
            print( "   ", target[a[0]:a[1]], q)
        print()
