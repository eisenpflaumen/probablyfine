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


##"uk" is ukrainian.
LANGS = ["None", "fr", "de", "pt", "lb", "uk"] #, "ar"]


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

@dataclass
class Fragment:
    kind: str      # "text" or "code"
    text: str

def parse_markdown(md: str):
    fragments = []
    pos       = 0
    n         = len(md)

    ## YAML front matter
    if md.startswith("---\n"):
        end = md.find("\n---\n", 4)
        if end != -1:
            end += len("\n---\n")
            fragments.append(
                Fragment( "code", md[:end] )
            )
            pos = end

    ## language switcher
    start_tag = r'<div class="language-switcher">'        
    end_tag   = r'<\div>'
    
    # Skip whitespace
    while pos < len(md) and md[pos].isspace(): 
        pos += 1

    switcher_end = -1    
    if md.startswith(start_tag, pos):
        switcher_start = pos
        switcher_end = md.find(r"</div>", switcher_start)
    
    if switcher_end == -1:
        raise ValueError("Unterminated language switcher")
    switcher_end += len(r"</div>")
    switcher = md[switcher_start:switcher_end]
    pos      = switcher_end
    ###edit the switcher block with string replacements, later.
    fragments.append( Fragment("code", switcher ) )

    text_start = pos
    while pos < n:

        ##single preserved characters
        if md[pos] in ">0123456789[]*#" or md[pos] == "\n":
            if text_start < pos:
                fragments.append(
                    Fragment("text", md[text_start:pos])
                )
            fragments.append(
                Fragment( "code", md[pos])
            )  
            pos        += 1
            text_start  = pos
            continue

        ##multichar formatting
        if pos <= len(md) - 3:
            if md[pos:pos+3]  == " - " or md[pos:pos+3]  == "---":
                if text_start < pos:
                    fragments.append(
                        Fragment("text", md[text_start:pos])
                    )
                fragments.append(Fragment( "code", md[pos:pos+3]))
                pos        += 3
                text_start  = pos
                continue

        # -------------------------
        # fenced code block
        # -------------------------
        if md.startswith("```", pos):
            if text_start < pos:
                fragments.append(
                    Fragment("text", md[text_start:pos])
                )
            end = md.find("```", pos + 3)
            if end == -1:
                end  = n
            else:
                end += 3
            fragments.append(
                Fragment("code", md[pos:end])
            )
            pos        = end
            text_start = pos
            continue

        # -------------------------
        # inline code or single-quoted string
        #
        # with awkward nested conditional to catch German "idiot's apostrophe".
        # -------------------------
        if md[pos] in  "`'" and\
            not( md[pos] == "'" and pos > 0 and md[pos-1] != " " ):
               
            if text_start < pos:
                fragments.append(
                    Fragment( "text", md[text_start:pos] )
                )
            end = md.find(md[pos], pos + 1)
            if end == -1:
                pos += 1
                continue
            end += 1
            fragments.append(
                Fragment( "code", md[pos:end])
            )
            pos        = end
            text_start = pos
            continue
 
        #
        # link text in markdown
        # 
        if pos > 0 and md[pos] == "(" and md[pos-1] == "]":
            if text_start < pos:
                fragments.append(
                    Fragment( "text", md[text_start:pos] )
                )
            end = md.find(")", pos + 1)
            if end == -1:
                pos += 1
                continue
            end += 1
            fragments.append(
                Fragment( "code", md[pos:end])
            )
            pos        = end
            text_start = pos
            continue

        #
        # Preserved acronymns (they are in french, but nobody cares)
        # 
        ackhit = False
        for ack in ["EIDE", "EIGT", "EIMAB", "LML"]:
            if md.startswith(ack, pos):
                if text_start < pos:
                    fragments.append(Fragment("text", md[text_start:pos]))
                end = pos + len(ack)
                fragments.append(Fragment("code", md[pos:end]))
                pos        = end
                text_start = pos
                ackhit = True
                break
        if ackhit is True: 
            continue
            
        ##not matched as start of a tag, so advance by 1
        pos += 1

      

    if text_start < n:
        fragments.append(
            Fragment(
                "text",
                md[text_start:]
            )
        )

    return fragments

def reconstruct(fragments):
    return "".join(
        f.text
        for f in fragments
    )

def translate(text: str, target_lang: str) -> str:
    fragments = parse_markdown(text)
    for f in fragments:
        if f.kind == "text" and f.text != " ":

            print("translating source text: %s to lang: %s" % (f.text, target_lang))

            cached = get_cached_translation( f.text, "en", target_lang)
            if cached:
                print("Cache hit:", cached)
                f.text = cached
            else:
                orig_text = f.text
                if target_lang != "None":
                    f.text    = translate_api( orig_text, target_lang ) 
                    print("Translation: ", f.text )              
                store_translation( orig_text, f.text, 'en', target_lang )
        if f.kind != "text" and "lang: en" in f.text:
            f.text = f.text.replace("lang: en", "lang: %s" % target_lang)

        if f.kind != "text" and "language-switcher" in f.text:
            lines = f.text.split("\n")
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
            f.text = "\n".join( lines_out ) 
            
    return reconstruct(fragments)

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
