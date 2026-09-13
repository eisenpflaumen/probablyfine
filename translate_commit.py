#!/usr/bin/env python3

"""
translate_commit.py

Workflow:

    ....edit a file, such as environment.md.....

    ###notify git it has been changed:
    git add environment.md

    ###translate and commit changes:
    ./translate_commit.py

    ....make sure it worked!....

    ###upload revised version
    git push

The script:

1. Finds staged files 
2. Generates translations into translations/<lang>/
3. Stages translated files
4. Creates a git commit

"""

from   pathlib import Path
import subprocess
import sys
import glob

##to be thrifty with API calls, cache any already-translated text.
import hashlib
import sqlite3
from typing import Optional
DB_FILE = "translation_cache.db"

##import local modules
import parse_markdown  as pmd

##The aligner has the annoying habit of checking online for weights updates
##every time the script is run. 
import align_sentences as align 


##"uk" is ukrainian.
LANGS = ["None", "fr", "de", "pt", "lb", "uk"] #, "ar"]

#LANGS = ["fr"]

##strings not to translate
do_not_translate =  {"EIDE", "EIGT", "EIMAB", "LML", "Liewen a Leieren"}

##override translation if the entire line is this:
fixed_translate = { ("Type", "fr") : "Type",
                  }

# -------------------------------------------------------------
# Translation backend
# -------------------------------------------------------------
from dataclasses import dataclass
import requests

##Never store the API key inside the project
API_KEY_FILE = "../probably_fine_translation_api_key.txt"
def load_api_key():
   with open(API_KEY_FILE, "r", encoding="utf-8") as f:
       return f.read().strip()

##probably shouldn't have it in global memory either but hey. Actual use of this key is quite restricted.
API_KEY = load_api_key()
import re
from dataclasses import dataclass

@dataclass
class AlignedWord:
    start: int
    end: int
    quality: float

@dataclass
class Insertion:
    words: list[AlignedWord]
    annotation: object

WORD_RE = re.compile(r"\S+")
def get_word_spans(text):
    """
    Returns:
        [(start,end), ...]
    """
    return [
        (m.start(), m.end())
        for m in WORD_RE.finditer(text)
    ]


def build_insertions(src_text, tgt_text, annotations, alignments):
    """
    It is a surprising faff to map one lot of words to another lot.

    annotations:
        list of Link/Emphasis objects, expressed
        in source character coordinates.

    alignments:
        dict mapping source word coordinates
        -> target word coordinates.

    Returns:
        list of Insertion objects sorted descending.
    """

    src_words = get_word_spans(src_text)
    tgt_words = get_word_spans(tgt_text)

    insertions = []
    for ann in annotations:
        print("\n processing annotation: ", ann)

        #
        # Find source words touched by this annotation
        # and match to target text.
        #
        aligned_tgt_words = []
        for src_word_start, src_word_end in src_words:

            overlaps = (src_word_end   > ann.start and
                        src_word_start < ann.end )
            w = src_text[src_word_start: src_word_end]
            if not overlaps:
                continue
            if (src_word_start,src_word_end) not in alignments:
                raise ValueError("error, no word alignment for %s" % w)
                
            ##possibly can have one-to-many mapping, for now just pick the best target word 
            (tgt_start,tgt_end), q = alignments[(src_word_start,src_word_end)][0]
            aligned_tgt_words.append( AlignedWord(tgt_start,tgt_end,q) )  

        if not aligned_tgt_words:
            raise ValueError("no alignment found for ", ann)

        print("raw alignment is to: ")
        for w in aligned_tgt_words: print("   "+tgt_text[w.start:w.end], w.quality)

        insertions.append( Insertion( annotation=ann, words=aligned_tgt_words ) )
     
    insertions.sort( key=lambda ins: max(w.quality for w in ins.words), reverse=True )
    return insertions

def remove_trapped_fragments( claimed ):
    """
    resolve conflicts / overlaps between text alignments
    """
    runs = [] ##a "run" is a contiguous block
    i    = 0
    while i < len(claimed): ##scan the list of source words claiming a target.
        if claimed[i] is None:
            i += 1
            continue

        ##continuous block
        owner = claimed[i][0]
        start = i
        while ( i < len(claimed) and
                claimed[i] is not None and
                claimed[i][0] == owner ):
            i += 1

        runs.append({"owner":owner, "start":start, "end":i, "length": i-start,})

    # Group runs by owner
    by_owner = {}
    for r in runs:
        by_owner.setdefault(id( r["owner"] ), []).append(r)

    #
    # For each owner keep the largest run.
    #
    for _, owner_runs in by_owner.items():
        if len(owner_runs) < 2:
            continue
        main = max(owner_runs, key=lambda r: r["length"])
        for r in owner_runs:
            if r is main:
                continue

            #
            # Check whether another annotation lies between
            # this run and the main run.
            #
            lo = min(r["end"], main["end"])
            hi = max(r["start"], main["start"])

            trapped = False
            for k in range(lo, hi):
                if claimed[k] is None:
                    continue
                if claimed[k][0] != owner:
                    trapped = True
                    break

            if trapped:
                for k in range(r["start"], r["end"]):
                    claimed[k] = None

    return claimed

def bridge_gaps(text, claimed, max_gap=56):

    """
    Close gaps where an annotation covers multiple words but misses "of"s or similar
    ambiguous or low-weight linking words
    """
    i = 0
    while i < len(claimed):
        # Find a gap.
        if claimed[i] is not None:
            i += 1
            continue
        gap_start = i
        while i < len(claimed) and claimed[i] is None:
            i += 1
        gap_end = i

        # Too large to bridge
        gap_len = gap_end - gap_start
        if gap_len > max_gap:
            continue

        # Gap at start/end of sentence.
        if gap_start == 0 or gap_end >= len(claimed):
            continue

        lhs     = claimed[gap_start - 1]
        rhs     = claimed[gap_end]
        if lhs is None or rhs is None:
            continue
        lhs_ins = lhs[0]
        rhs_ins = rhs[0]

        # Only bridge same annotation.
        if lhs_ins != rhs_ins:
            continue
        
        # Don´t cross a newline
        if "\n" in text[gap_start:gap_end]:
            continue

        # Fill the gap.
        q = min(lhs[2], rhs[2])
        for j in range(gap_start, gap_end):
            claimed[j] = (lhs_ins, None, q)

    return claimed

def enforce_uniqueness(claimed):

    """
    Make sure that a given annotation is only inserted once.
    """
    runs = []
    i    = 0
    while i < len(claimed):

        if claimed[i] is None:
            i += 1
            continue
        owner = claimed[i][0]
        start = i
        while ( i < len(claimed)
                and claimed[i] is not None
                and claimed[i][0] == owner ):
            i += 1

        runs.append(
            (owner, start, i, i - start)
        )

    #
    # Group by owner.
    #
    by_owner = {}
    for r in runs:
        owner = id( r[0] )
        by_owner.setdefault(owner, []).append(r)

    #
    # Keep largest run only.
    #
    for _, owner_runs in by_owner.items():
        if len(owner_runs) < 2:
            continue
        main = max(owner_runs, key=lambda r: r[3])
        for r in owner_runs:
            if r is main:
                continue
            _, start, end, _ = r
            for i in range(start, end):
                claimed[i] = None

    return claimed

def extract_insertions(claimed, text):
    """
    convert the vector of character claims back into a list of insertions to make
    """
    runs = []
    i    = 0
    while i < len(claimed):

        if claimed[i] is None:
            i += 1
            continue

        ann   = claimed[i][0].annotation
        start = i
        while (i < len(claimed) and
               claimed[i] is not None and
               claimed[i][0].annotation == ann):
            i += 1

        ##don´t annotate trailing whitespace
        ii = i 
        while ii > start and text[ii-1].isspace():
           ii -= 1
        print("appending annotation run: "+text[start:ii])
        
        runs.append((start, ii, ann))

    return runs


def chop_at_linebreak(claimed, text):
    """
    Enforce no multiline annotations.
    If an annotation spans multiple lines, keep only the
    highest-confidence fragment.
    """

    i = 0
    while i < len(claimed):

        if claimed[i] is None:
            i += 1
            continue

        owner = claimed[i][0]
        start = i
        while (i < len(claimed) and
               claimed[i] is not None and
               claimed[i][0] == owner):
            i += 1
        end = i
        if "\n" not in text[start:end]:
            continue

        frags  = []
        fstart = start
        score  = 0.0
        for j in range(start, end):

            if claimed[j] is not None:
                score += claimed[j][2]
            if text[j] == "\n":
                frag_end = j
                while (fstart < frag_end and
                       text[fstart] in "\r\n"):
                    fstart += 1
                while (frag_end > fstart and
                       text[frag_end - 1] in "\r\n"):
                    frag_end -= 1
                frags.append((fstart, frag_end, score))
                fstart = j + 1
                score  = 0.0

        #
        # Final fragment.
        #
        frag_end = end
        while (fstart < frag_end and
               text[fstart] in "\r\n"):
            fstart += 1
        while (frag_end > fstart and
               text[frag_end - 1] in "\r\n"):
            frag_end -= 1
        frags.append((fstart, frag_end, score))
        keep = max(frags, key=lambda f: f[2])

        for s, e, q in frags:
            if (s, e, q) == keep:
                continue
            for j in range(s, e):
                claimed[j] = None

    return claimed

def rebuild_chunk(chunk, target_lang = None):
    """
    Take a Chunk dataclass object, with text and markdown parsed-out
    and reassemble it into marked-down (marked-up) text.
    """
    if target_lang is not None:
       alignment_map = align.align( chunk.text, chunk.new_text )
    else:
       ##build a trivial alignment map
       matches = list(re.finditer(r'\S+', chunk.text))
       src_offsets   = [(m.start(), m.end()) for m in matches]
       alignment_map = {}
       for s in src_offsets:
           alignment_map[s] = (s, 1.) ##unique alignment with max quality 1.


    text       = chunk.new_text
    insertions = build_insertions(chunk.text, chunk.new_text, chunk.annotations, alignment_map)

    #for each char in the new text, log which word has claimed it and with what 
    #confidence level.
    claimed = [None] * len(text)
    for ins in insertions: ##sorting downwards by insertion confidence
        ann = ins.annotation
        for idx, w in enumerate( ins.words ):
            survives = False #this flag tracks if some part of the insertion is present
            for i in range(w.start, w.end):
               old   = claimed[i]
               old_q = 0.
               if old is not None:
                   old_q = old[2]
               if old is None or w.quality > old_q:
                   claimed[i] = (ins, idx, w.quality)
                   survives = True 
            w.survives = survives     
    
    # (single) repair pass:
    # any annotation with zero surviving words gets its best word back.
    #
    for ins in insertions:                        
        if any(w.survives for w in ins.words): continue
        best_idx = max( range(len(ins.words)),
                        key=lambda i: ins.words[i].quality )
        best          = ins.words[best_idx]
        best.survives = True
        for i in range(best.start, best.end):
            claimed[i] = (ins, best_idx, best.quality)
        print("resolved(?) clash. Claimed word is: ", best) 

    ## resolve interleaved annotations
    claimed = remove_trapped_fragments( claimed )
    
    ## fill small gaps in annotations
    claimed = bridge_gaps( text, claimed )

    ##delete any remaining stragglers
    claimed = enforce_uniqueness( claimed ) 

    ## for now, enforce no multiline annotations
    claimed = chop_at_linebreak( claimed, text )
    
    to_ins  = extract_insertions( claimed, text ) ##return as list of tuples (start, end, annotation)
    to_ins.sort(key=lambda x: x[0], reverse=True)
    for start, end, ann in to_ins:
        ##URL 
        if isinstance(ann, pmd.Link):
 
            print( "adding annotation: ", ann )
            print( "to text: ", text[start:end] )

            body = text[start:end]
            text = text[:start] +\
                     "[" + text[start:end] + "]" +\
                     "(" + ann.url + ")" +\
                   text[end:] 

        ##emphasis (or in general, paired markers wrapped around text)
        elif isinstance(ann, pmd.Emphasis):
            body = text[start:end]
            text = text[:start] +\
                     ann.marker + body + ann.marker  +\
                      text[end:] 

    ##prefix annotations like ">" for a markdown block quote.
    chunk.new_mdtext = chunk.prefix + text

    return chunk

def translate( text, target_lang: str) -> str:

    chunks = pmd.parse_markdown(text)

    for c in chunks:

        if c.skip or len(c.text) == 0:
            print("pass through: %r" % c.old_mdtext)
            c.new_mdtext = c.old_mdtext

            ###hacks to update the language metadata for the page.
            if "lang: en" in c.new_mdtext:
                c.new_mdtext = c.new_mdtext.replace("lang: en", "lang: %s" % target_lang)

            if "language-switcher" in c.new_mdtext:
                lines = c.new_mdtext.split("\n")
                lines_out = []
                current   = False
                for L in lines:
                    if 'class="current"' in L:
                        L = L.replace( 'class="current"', '' )
                    if current is True:
                        L = L.replace( '">', '" class="current">' )
                        current = False
                    if "translations/%s" % target_lang in L:
                        current = True
                    lines_out.append( L )
                c.new_mdtext = "\n".join( lines_out )
            continue
      
        key = (c.text.strip(), target_lang) 
        if key in fixed_translate:
            c.new_text = fixed_translate[key]
            print("hardcoded override: source:%r to %s: %r" % (c.text, target_lang, c.new_text))
            target_lang = "None"
        else: 
            ##save not just translated text but translated+annotated
            ##because parsing annotations requires sentence alignment, which is slow.
            cached = get_cached_translation( c.old_mdtext, "en", target_lang)
            if cached:
                print("Cache hit: %r to %r" % (c.old_mdtext, cached) )
                c.new_mdtext = cached
                continue

        ##if we get this far then we have to do some actual work.
        orig_text = c.text
        if target_lang != "None":

            ##translate the raw text
            c.new_text    = translate_api( c.text, target_lang ) 
            print("source:%r to %s: %r" % (c.text, target_lang, c.new_text))
        else:
            c.new_text = c.text

        ##need to align and rebuild the markdown.
        ##prefixes and annotations (including urls) are re-added here.
        rebuild_chunk( c, target_lang )

        ##save the translated text to cache
        if target_lang != None:
            store_translation( c.old_mdtext, c.new_mdtext, 'en', target_lang )
           
    out_text = ""
    for c in chunks:
       out_text = out_text + c.new_mdtext + c.suffix 
 
    return out_text


def translate_api( text, target_lang ):
    """
    Build and post a json request to translate some text.

    """ 
    response = requests.post( "https://translation.googleapis.com/language/translate/v2",
       params  = { "key": API_KEY, }, json = { "q": text, "target": target_lang, }, timeout = 30, )

    response.raise_for_status()

    return response.json()["data"]["translations"][0]["translatedText"]

# -------------------------------------------------------------
# Git helpers
# -------------------------------------------------------------

def run(*args):
    return subprocess.run(
        args,
        capture_output=True,
        text=True,
        check=True,
    )


def get_staged_files():
    result = run(
        "git",
        "diff",
        "--cached",
        "--name-only",
    )

    retlist = []
    for line in result.stdout.splitlines():
       if "translations" in line:
           continue ##skip already translated text
       if line.strip():
           retlist.append( line )

    return retlist

# -------------------------------------------------------------
# Translation
# -------------------------------------------------------------

def translate_file( source_file: Path, languages = LANGS ):

    source_text = source_file.read_text(
        encoding="utf-8"
    )
    # remove leading "/"
    relative_path = Path(*source_file.parts[:])

    generated_files = []

    for lang in languages:

        target_file = (
            Path("translations")
            / lang
            / relative_path
        )

        target_file.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        translated_text = translate( source_text, lang )

        target_file.write_text(
            translated_text,
            encoding="utf-8",
        )

        generated_files.append(target_file)

    return generated_files


# -------------------------------------------------------------
# Main
# -------------------------------------------------------------

def main( args ):

    if args.force:
        print( "forcing translation of files:", args.force )
        staged = [ Path(f) for f in args.force ]
        ##staged = staged + get_staged_files()
    else:
        staged = get_staged_files()
    print("total staged files: ", staged)

    english_files = []
    generated     = []
    for p in staged:
        suffix = str(p).split(".")[-1]
        if suffix.lower() in ("md", "markdown"): 
            ##do we need to check that it is in English? 
            english_files.append( p )

    if not english_files:
        print("No files to translate.")

    ###translate any modified english text
    init_cache() ##load if pre-exisiting, save if done for the first time.

    for src in english_files:
        print( f"Translating: {src}" )

        languages = LANGS
        if args.no_translate:
            languages = ["None"]        

        
        generated.extend( translate_file( Path(src), languages) )

    # stage generated translations
    for p in generated:
        subprocess.run(
            ["git", "add", str(p)],
            check=True,
        )

    # create commit
    if not args.no_commit:
        msg = (args.message or "Update content and translations")
        subprocess.run(
           ["git", "commit", "-m", msg], check=True )

        print()
        print("Committed:")
        for p in staged:
            print(f"  {p}")

    print()
    print(
        f"Generated {len(generated)} translation files."
    )
    for f in generated:
        print(f)


#############translation caching stuff:
def init_cache():
    conn = sqlite3.connect(DB_FILE)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS translations (
            text_hash TEXT PRIMARY KEY,
            source_lang TEXT,
            target_lang TEXT,
            original_text TEXT,
            translated_text TEXT
        )
    """)
    conn.commit()
    conn.close()

def text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()

def get_cached_translation( text: str, source_lang: str, target_lang: str ): 

    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()

    h = text_hash(f"{source_lang}|{target_lang}|{text}")
    cur.execute("""
        SELECT translated_text
        FROM translations
        WHERE text_hash = ?
    """, (h,))

    row = cur.fetchone()
    conn.close()

    return row[0] if row else None


def store_translation(
    text: str,
    translated_text: str,
    source_lang: str,
    target_lang: str
):

    h = text_hash(f"{source_lang}|{target_lang}|{text}")

    conn = sqlite3.connect(DB_FILE)

    conn.execute("""
        INSERT OR REPLACE INTO translations
        (
            text_hash,
            source_lang,
            target_lang,
            original_text,
            translated_text
        )
        VALUES (?, ?, ?, ?, ?)
    """, (h, source_lang, target_lang, text, translated_text))

    conn.commit()
    conn.close()


import argparse
if __name__ == "__main__":

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--no-commit",
         action="store_true",
         help="Generate translations but do not commit")
    parser.add_argument(
        "--force",
        nargs="+",
        metavar="FILE",
        help="Force translation of specified English files"
    )
    parser.add_argument(
        "--no-translate",
        action="store_true",
        help="Debug to save API calls: do not *actually* translate the files"
    )

    parser.add_argument("-m", "--message", help="Commit message")
    args = parser.parse_args()

    main( args )
