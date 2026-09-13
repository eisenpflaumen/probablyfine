#!/usr/bin/env python3
import numpy as np
import re
from typing import Optional
from dataclasses import dataclass

"""
Translator for markdown text.
"""
@dataclass
class Link:
    start: int
    end:   int
    url:   str

@dataclass
class Emphasis:
    start:  int
    end:    int
    marker: str 

@dataclass
class Chunk:
    prefix:      str
    text:        str
    new_mdtext:  str
    old_mdtext:  str
    annotations: list
    skip:        bool
    suffix:      str = "" 

EMPH_RE = re.compile(
    r'(\*\*\*|\*\*|\*)(.*?)\1'
)

def parse_emphasis(chunk):
    """
    Replace markdown emphasis with plain text and add
    Emphasis(start,end) annotations to the chunk which can be restored in the translated
    and word-matched version.
    """

    text        = chunk.text
    annotations = []
    output      = []

    src_pos = 0
    dst_pos = 0

    for m in EMPH_RE.finditer(text):

        start, end = m.span()

        # literal text before emphasis
        literal = text[src_pos:start]
        output.append(literal)
        dst_pos += len(literal)

        # emphasised text
        body      = m.group(2)
        marker    = m.group(1)
        ann_start = dst_pos
        ann_end   = dst_pos + len(body)

        annotations.append(
            Emphasis(
                start  = ann_start,
                end    = ann_end,
                marker = marker
            )
        )

        output.append(body)

        dst_pos += len(body)
        src_pos = end

    # trailing text
    tail = text[src_pos:]
    output.append(tail)

    chunk.text = "".join(output)
    chunk.annotations.extend(annotations)

    return chunk


LINK_RE = re.compile(
    r'\[([^\]]+)\]\(([^)]+)\)'
)
def parse_links(chunk):
    """
    Replace markdown links with plain link text and add
    Link(start,end,url) annotations.
    """

    text = chunk.text
    annotations = []
    output      = []

    src_pos = 0
    dst_pos = 0

    for m in LINK_RE.finditer(text):

        start, end = m.span()

        # ordinary text before link
        literal = text[src_pos:start]

        output.append(literal)

        dst_pos  += len(literal)
        link_text = m.group(1)
        url       = m.group(2)

        ann_start = dst_pos
        ann_end   = dst_pos + len(link_text)

        annotations.append(
            Link( start=ann_start, end=ann_end, url=url )
        )

        output.append(link_text)

        dst_pos += len(link_text)
        src_pos  = end

    # trailing text
    tail = text[src_pos:]
    output.append(tail)

    chunk.text = "".join(output)
    chunk.annotations.extend(annotations)

    return chunk

# regexp for tables. These contain lots of --- which look like dividers
# so check for tables first, then dividers more generally.
TABLE_DIV_RE = re.compile(r'^\s*\|(?:\s*:?-+:?\s*\|)+\s*$')
TABLE_ROW_RE = re.compile(r'^\s*\|.*\|\s*$')

## regext for --- *** : when unpaired, these indicate dividers.
DIVIDER_RE = re.compile(  r'^(\s*[-*_]{3,}\s*\n)' )
    
## regexp to pick up block quotes, lists etc. >, 1. etc.
PREFIX_RE = re.compile( r'^(#{1,6}\s+|>\s*|[-*+]\s+|\d+\.\s+)' )

def parse_markdown(md):
    """
    Convert a block of text to chunks, with annotation for markdown syntax.
    """
    #
    # Split into paragraphs.
    #
    paragraphs = [
        p for p in re.split(r'(\n\s*\n)', md) if p ]
    paras      = []
    for p in paragraphs:
        # Further split whenever table column begins
        pieces = re.split(r'(\||\n)', p)
        for piece in pieces:
            if len(piece) < 1024:
                paras.append(piece)
                continue
            
            # Rare case: paragraph exceeds API limit.
            sentences = piece.split(".")
            for s in sentences:
               s = s.strip()
               if s:
                  paras.append(s + ".")

    chunks_to_translate = []
    have_header = False
    in_header   = False
    for para in paras:
        skip = False

        ##header info
        m = DIVIDER_RE.match(para)
        if have_header is False:
            if in_header is False:
               if "---" in para:
                  in_header = True
            elif "---" in para:
               in_header   = False
               have_header = True
            chunks_to_translate.append(
               Chunk( prefix="", text=para, new_mdtext=None, old_mdtext=para, 
                 annotations=[], skip=True )
            )
            continue

        ###line is nothing else but just divider
        elif re.match(r'^\s*([-*_])\1{2,}\s*$', para):
            skip = True

        ##suspect umatched dashes:
        elif "---" in para:
            raise ValueError("unmatched --- in: '%r' " % para)
          
        ##lazy check for html or table markdown 
        if para.lstrip().startswith("<"):
            skip = True

        if para == "|":
            skip = True

        ##if it is pure whitespace
        if not para.strip():
            skip = True

        ##complicated regex to check for line-prefix markdown
        m = PREFIX_RE.match(para)
        if m and not skip:
            prefix = m.group(1)
            body   = para[len(prefix):]
        else:
            prefix = ""
            body   = para

        ##save it as a "Chunk" dataclass
        chunks_to_translate.append( 
             Chunk( prefix = prefix, text = body, new_mdtext = None, 
                              old_mdtext = para, annotations = [], skip=skip ) )

        ##modify text and annotations fields in-place.
        parse_emphasis( chunks_to_translate[-1] )
        parse_links( chunks_to_translate[-1] )

    return chunks_to_translate

if __name__ == "__main__":
    """minimal test"""

    text = open( "index.md", "r" ).read()
    chunks = parse_markdown( text )

    for c in chunks:
        print( c )



