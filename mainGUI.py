#!/usr/bin/env python3

"""
OpenAlex Paper Fetcher - Modern GUI with PyQt6
Version: 1.0
Features: Keyword search, year filter, progress tracking, file management, Dark/Light theme, Paper Explorer with removal
"""

import sys
import os
import json
import re
import time
import requests
import pandas as pd
from datetime import datetime, timedelta
from pathlib import Path
from tqdm import tqdm
import threading
import webbrowser
import copy
from queue import Queue
from difflib import SequenceMatcher
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from PyQt6.QtWidgets import *
from PyQt6.QtCore import *
from PyQt6.QtGui import *

# ==========================================================
# Backend Functions
# ==========================================================

VERSION = "1.0"

def abstract_from_index(inv):
    """Reconstruct abstract from OpenAlex inverted index"""
    if not inv:
        return ""
    
    words = {}
    max_pos = 0
    
    for word, positions in inv.items():
        for p in positions:
            words[p] = word
            max_pos = max(max_pos, p)
    
    abstract_parts = []
    for i in range(max_pos + 1):
        if i in words:
            abstract_parts.append(words[i])
    
    return " ".join(abstract_parts)

def extract_keywords_from_paper(paper):
    """Extract keywords from OpenAlex data"""
    keywords = []
    
    concepts = paper.get("concepts", [])
    if concepts:
        for concept in concepts[:5]:
            if concept and isinstance(concept, dict):
                name = concept.get("display_name")
                if name:
                    keywords.append(name)
    
    paper_keywords = paper.get("keywords", [])
    if paper_keywords:
        for kw in paper_keywords:
            if kw and isinstance(kw, dict):
                name = kw.get("display_name")
                if name:
                    keywords.append(name)
    
    unique_keywords = list(dict.fromkeys(keywords))[:6]
    return unique_keywords

def clean_bibtex_field(value):
    """Clean value for BibTeX field"""
    if not value:
        return ""
    value = str(value)
    value = value.replace("{", "\\{")
    value = value.replace("}", "\\}")
    value = value.replace("\n", " ")
    value = value.replace("%", "\\%")
    value = value.replace("&", "\\&")
    value = value.replace("_", "\\_")
    value = value.replace("#", "\\#")
    return value.strip()

def generate_bibtex_key(paper):
    """Generate a clean BibTeX key"""
    authorships = paper.get("authorships", [])
    if authorships and isinstance(authorships, list):
        first_author = authorships[0].get("author", {}).get("display_name", "") if authorships else ""
        surname = first_author.split()[-1] if first_author else "unknown"
    else:
        surname = "unknown"
    
    year = paper.get("publication_year", datetime.now().year)
    
    title = paper.get("title", "")
    title_words = title.split() if title else []
    skip_words = {'a', 'an', 'the', 'on', 'of', 'for', 'with', 'using', 'toward', 'towards', 'and', 'in', 'to'}
    first_word = next((word for word in title_words if word.lower() not in skip_words), "paper")
    first_word = re.sub(r'[^a-zA-Z0-9]', '', first_word)
    
    key = f"{surname}{year}{first_word[:5]}"
    key = re.sub(r'[^a-zA-Z0-9]', '', key)
    return key

def generate_ai_summary(title, abstract, keywords):
    """Generate a concise AI-friendly summary"""
    if not abstract:
        return f"Paper on {title[:60]}..."
    
    sentences = abstract.split('. ')
    key_sentence = sentences[0] if sentences else abstract
    
    summary = key_sentence[:200]
    if len(key_sentence) > 200:
        summary = summary + "..."
    
    if keywords:
        summary += f" Keywords: {', '.join(keywords[:3])}."
    
    return summary

def paper_to_bibtex(paper, include_full=False, include_ai_summary=False, include_abstract=False):
    """Convert paper to BibTeX entry"""
    title = paper.get("title", "") or ""
    year = paper.get("publication_year", "") or ""
    doi = paper.get("doi", "") or ""
    
    authorships = paper.get("authorships", [])
    authors = []
    if authorships and isinstance(authorships, list):
        for auth in authorships:
            if auth and isinstance(auth, dict):
                author_name = auth.get("author", {}).get("display_name", "") if auth.get("author") else ""
                if author_name:
                    parts = author_name.split()
                    if len(parts) > 1:
                        last = parts[-1]
                        initials = " ".join([f"{p[0]}." for p in parts[:-1]])
                        authors.append(f"{last}, {initials}")
                    else:
                        authors.append(author_name)
    
    author_str = " and ".join(authors) if authors else "{Unknown Author}"
    
    primary_location = paper.get("primary_location")
    source = primary_location.get("source") if primary_location and isinstance(primary_location, dict) else None
    venue = source.get("display_name", "") if source and isinstance(source, dict) else ""
    
    if not venue:
        host_venue = paper.get("host_venue")
        venue = host_venue.get("display_name", "") if host_venue and isinstance(host_venue, dict) else ""
    
    volume = paper.get("volume", "") or ""
    issue = paper.get("issue", "") or ""
    pages = paper.get("pages", "") or ""
    if not pages:
        biblio = paper.get("biblio")
        if biblio and isinstance(biblio, dict):
            first_page = biblio.get("first_page", "")
            last_page = biblio.get("last_page", "")
            if first_page and last_page:
                pages = f"{first_page}--{last_page}"
    
    abstract = abstract_from_index(paper.get("abstract_inverted_index"))
    keywords = extract_keywords_from_paper(paper)
    citedby = paper.get("cited_by_count", 0)
    openalex_id = paper.get("id", "") or ""
    
    key = generate_bibtex_key(paper)
    
    pub_type = paper.get("type", "article-journal") or "article-journal"
    entry_type = {
        "article-journal": "article",
        "article": "article",
        "book": "book",
        "book-chapter": "incollection",
        "proceedings-article": "inproceedings",
        "dissertation": "phdthesis",
        "report": "techreport",
        "preprint": "misc",
        "dataset": "misc"
    }.get(pub_type, "misc")
    
    bib = f"@{entry_type}{{{key},\n"
    bib += f"  author    = {{{author_str}}},\n"
    bib += f"  title     = {{{clean_bibtex_field(title)}}},\n"
    if venue:
        bib += f"  journal   = {{{clean_bibtex_field(venue)}}},\n"
    if volume:
        bib += f"  volume    = {{{volume}}},\n"
    if issue:
        bib += f"  number    = {{{issue}}},\n"
    if pages:
        bib += f"  pages     = {{{pages}}},\n"
    if year:
        bib += f"  year      = {{{year}}},\n"
    if doi:
        bib += f"  doi       = {{{doi}}},\n"
    
    if include_abstract and abstract:
        bib += f"  abstract  = {{{clean_bibtex_field(abstract)}}},\n"
    
    if include_full:
        if keywords:
            bib += f"  keywords  = {{{clean_bibtex_field(', '.join(keywords))}}},\n"
        if citedby:
            bib += f"  citedby   = {{{citedby}}},\n"
        if openalex_id:
            bib += f"  openalex_id = {{{openalex_id}}},\n"
    
    if include_ai_summary:
        summary = generate_ai_summary(title, abstract, keywords)
        bib += f"  summary   = {{{clean_bibtex_field(summary)}}},\n"
    
    bib = bib.rstrip(",\n") + "\n}"
    return bib

def fetch_papers_with_filters(query, max_results=50, year_start=None, year_end=None, progress_callback=None):
    """Fetch papers with year filter"""
    URL = "https://api.openalex.org/works"
    all_papers = []
    per_page = min(20, max_results)
    cursor = "*"
    
    # Build filter
    filter_parts = []
    if year_start and year_end:
        filter_parts.append(f"publication_year:{year_start}-{year_end}")
    elif year_start:
        filter_parts.append(f"publication_year:>{year_start}")
    elif year_end:
        filter_parts.append(f"publication_year:<{year_end}")
    
    filter_str = ",".join(filter_parts) if filter_parts else ""
    
    with tqdm(total=max_results, desc="Fetching papers") as pbar:
        while len(all_papers) < max_results:
            params = {
                "search": query,
                "per-page": per_page,
                "cursor": cursor
            }
            if filter_str:
                params["filter"] = filter_str
            
            try:
                r = requests.get(URL, params=params, timeout=30)
                r.raise_for_status()
                data = r.json()
                results = data.get("results", [])
                
                if not results:
                    break
                
                all_papers.extend(results)
                pbar.update(len(results))
                
                if progress_callback:
                    progress_callback(len(all_papers), max_results)
                
                cursor = data.get("meta", {}).get("next_cursor")
                if not cursor:
                    break
                    
            except Exception as e:
                if progress_callback:
                    progress_callback(-1, 0, str(e))
                break
    
    return all_papers[:max_results]

def filter_papers_with_abstract(papers):
    """Filter papers that have an abstract"""
    filtered = []
    for paper in papers:
        abstract = abstract_from_index(paper.get("abstract_inverted_index"))
        if abstract and len(abstract.strip()) > 0:
            filtered.append(paper)
    return filtered

def filter_and_sort_by_citations(papers):
    """Filter papers that have at least one citation, sorted by citation count (high to low)"""
    filtered = [p for p in papers if p.get("cited_by_count", 0) > 0]
    filtered.sort(key=lambda p: p.get("cited_by_count", 0), reverse=True)
    return filtered

def filter_journal_papers(papers):
    """Keep only journal articles (OpenAlex type 'article'), excluding theses,
    conference papers, preprints, books, datasets, etc."""
    return [p for p in papers if p.get("type") == "article"]

def save_all_outputs(papers, output_dir, query, only_with_abstract=False, only_with_citations=False, only_journal_papers=False):
    """Generate all output files"""
    # Create output directory
    Path(output_dir).mkdir(parents=True, exist_ok=True)

    # Filter papers if requested
    if only_with_abstract:
        original_count = len(papers)
        papers = filter_papers_with_abstract(papers)
        filtered_count = len(papers)
        print(f"Filtered: {original_count} -> {filtered_count} papers with abstracts")

    # Filter to journal articles only (no thesis, no conference papers) if requested
    if only_journal_papers:
        original_count = len(papers)
        papers = filter_journal_papers(papers)
        filtered_count = len(papers)
        print(f"Filtered: {original_count} -> {filtered_count} journal papers (no thesis/conference)")

    # Filter to papers with citations and sort high to low if requested
    if only_with_citations:
        original_count = len(papers)
        papers = filter_and_sort_by_citations(papers)
        filtered_count = len(papers)
        print(f"Filtered: {original_count} -> {filtered_count} papers with citations (sorted high to low)")

    # Generate BibTeX files
    clean_entries = []
    abstract_entries = []
    full_entries = []
    summary_entries = []
    
    for paper in papers:
        try:
            clean_bib = paper_to_bibtex(paper, include_full=False, include_ai_summary=False, include_abstract=False)
            clean_entries.append(clean_bib)
            
            abstract_bib = paper_to_bibtex(paper, include_full=False, include_ai_summary=False, include_abstract=True)
            abstract_entries.append(abstract_bib)
            
            full_bib = paper_to_bibtex(paper, include_full=True, include_ai_summary=False, include_abstract=True)
            full_entries.append(full_bib)
            
            summary_bib = paper_to_bibtex(paper, include_full=True, include_ai_summary=True, include_abstract=True)
            summary_entries.append(summary_bib)
        except Exception as e:
            continue
    
    # Save files
    files = {}
    files['filtered_count'] = len(papers)
    files['original_count'] = len(papers)
    
    clean_file = os.path.join(output_dir, "refs.bib")
    with open(clean_file, 'w', encoding='utf8') as f:
        for entry in clean_entries:
            f.write(entry)
            f.write("\n\n")
    files['clean'] = clean_file
    
    abstract_file = os.path.join(output_dir, "refs_abstract.bib")
    with open(abstract_file, 'w', encoding='utf8') as f:
        for entry in abstract_entries:
            f.write(entry)
            f.write("\n\n")
    files['abstract'] = abstract_file
    
    full_file = os.path.join(output_dir, "refs_full.bib")
    with open(full_file, 'w', encoding='utf8') as f:
        for entry in full_entries:
            f.write(entry)
            f.write("\n\n")
    files['full'] = full_file
    
    summary_file = os.path.join(output_dir, "refs_with_summary.bib")
    with open(summary_file, 'w', encoding='utf8') as f:
        for entry in summary_entries:
            f.write(entry)
            f.write("\n\n")
    files['summary'] = summary_file
    
    # Save JSON
    json_file = os.path.join(output_dir, "papers.json")
    with open(json_file, 'w', encoding='utf8') as f:
        json.dump(papers, f, indent=2)
    files['json'] = json_file
    
    # Save Excel
    excel_data = []
    for paper in papers:
        try:
            authors = []
            authorships = paper.get("authorships", [])
            if authorships and isinstance(authorships, list):
                for auth in authorships[:5]:
                    if auth and isinstance(auth, dict):
                        name = auth.get("author", {}).get("display_name", "") if auth.get("author") else ""
                        if name:
                            authors.append(name)
            
            author_str = "; ".join(authors)
            if len(authorships) > 5:
                author_str += f" et al. ({len(authorships)} authors)"
            
            abstract = abstract_from_index(paper.get("abstract_inverted_index"))
            keywords = extract_keywords_from_paper(paper)
            
            primary_location = paper.get("primary_location")
            source = primary_location.get("source") if primary_location and isinstance(primary_location, dict) else None
            journal = source.get("display_name", "") if source and isinstance(source, dict) else ""
            
            row = {
                "Title": paper.get("title", ""),
                "Authors": author_str,
                "Year": paper.get("publication_year", ""),
                "Journal": journal,
                "DOI": paper.get("doi", ""),
                "Cited By": paper.get("cited_by_count", 0),
                "Abstract": abstract[:500] + "..." if len(abstract) > 500 else abstract,
                "Keywords": ", ".join(keywords),
                "OpenAlex ID": paper.get("id", ""),
                "Type": paper.get("type", "")
            }
            excel_data.append(row)
        except Exception:
            continue
    
    if excel_data:
        excel_file = os.path.join(output_dir, "papers.xlsx")
        df = pd.DataFrame(excel_data)
        df.to_excel(excel_file, index=False, engine='openpyxl')
        files['excel'] = excel_file
    
    # Save abstracts.md
    md_file = os.path.join(output_dir, "abstracts.md")
    with open(md_file, 'w', encoding='utf8') as f:
        f.write("# Literature Review Notes\n\n")
        f.write(f"*Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}*\n\n")
        f.write(f"**Total Papers:** {len(papers)}\n\n")
        if only_with_abstract:
            f.write(f"**Filtered:** Only papers with abstracts included\n\n")
        f.write("---\n\n")
        
        for i, paper in enumerate(papers, 1):
            try:
                authors = []
                authorships = paper.get("authorships", [])
                if authorships and isinstance(authorships, list):
                    for auth in authorships[:3]:
                        if auth and isinstance(auth, dict):
                            name = auth.get("author", {}).get("display_name", "") if auth.get("author") else ""
                            if name:
                                authors.append(name)
                
                author_str = ", ".join(authors)
                if len(authorships) > 3:
                    author_str += f" et al."
                
                abstract = abstract_from_index(paper.get("abstract_inverted_index"))
                keywords = extract_keywords_from_paper(paper)
                citedby = paper.get("cited_by_count", 0)
                doi = paper.get("doi", "")
                
                primary_location = paper.get("primary_location")
                source = primary_location.get("source") if primary_location and isinstance(primary_location, dict) else None
                journal = source.get("display_name", "") if source and isinstance(source, dict) else ""
                
                f.write(f"## {i}. {paper.get('title', 'Untitled')}\n\n")
                f.write(f"**Authors:** {author_str or 'Unknown'}\n\n")
                f.write(f"**Year:** {paper.get('publication_year', 'N/A')}\n\n")
                f.write(f"**Journal:** {journal or 'N/A'}\n\n")
                f.write(f"**Citations:** {citedby}\n\n")
                if doi:
                    f.write(f"**DOI:** [{doi}](https://doi.org/{doi})\n\n")
                if keywords:
                    f.write(f"**Keywords:** {', '.join(keywords)}\n\n")
                
                f.write("**Abstract:**\n\n")
                if abstract:
                    f.write(f"{abstract}\n\n")
                else:
                    f.write("*No abstract available*\n\n")
                
                summary = generate_ai_summary(paper.get('title', ''), abstract, keywords)
                f.write("**AI Summary:**\n\n")
                f.write(f"> {summary}\n\n")
                
                f.write("---\n\n")
            except Exception:
                continue
    
    files['md'] = md_file
    
    return files

# ==========================================================
# BibTeX Validation / Reference Checker Backend
# ==========================================================

# Confidence / similarity thresholds (conservative by design)
BIB_CONF_VALID = 0.85          # overall confidence considered fully VALID
BIB_CONF_MINOR = 0.65          # overall confidence considered acceptable with minor differences
BIB_CONF_WEAK = 0.40           # below this -> treat match as unreliable
BIB_TITLE_STRONG = 0.75        # title similarity considered a strong match
BIB_TITLE_MISMATCH = 0.50      # title similarity below this + DOI match -> metadata mismatch warning

CROSSREF_HEADERS = {
    'User-Agent': 'RefsFinder-BibValidator/1.0 (mailto:refsfinder@example.com)'
}

BIB_VALID_STATUSES = {'VALID', 'VALID - minor metadata difference'}
BIB_WARNING_STATUSES = {'DOI VALID - metadata mismatch', 'SUSPICIOUS'}
BIB_INVALID_STATUSES = {'DOI NOT FOUND', 'PAPER NOT FOUND'}
BIB_DUPLICATE_STATUSES = {'POSSIBLE DUPLICATE'}
BIB_UNVERIFIED_STATUSES = {'UNVERIFIED', 'INSUFFICIENT DATA', 'PENDING'}

BIB_FIELD_ORDER = ['author', 'title', 'journal', 'booktitle', 'volume', 'number',
                    'pages', 'year', 'doi', 'url', 'publisher', 'abstract', 'keywords', 'note']


def parse_bib_file(path):
    """Parse a .bib file into a list of entry dicts: {'key','type','fields'}.
    Robust, hand-rolled parser (handles nested braces); malformed entries are
    skipped individually and reported in the returned errors list instead of
    crashing the whole parse.
    """
    with open(path, 'r', encoding='utf-8-sig', errors='replace') as f:
        content = f.read()

    entries = []
    errors = []
    i = 0
    n = len(content)

    while i < n:
        at_pos = content.find('@', i)
        if at_pos == -1:
            break

        m = re.match(r'@(\w+)\s*\{', content[at_pos:], re.IGNORECASE)
        if not m:
            i = at_pos + 1
            continue

        entry_type = m.group(1).lower()
        brace_start = at_pos + m.end() - 1  # index of the opening '{'

        depth = 0
        j = brace_start
        while j < n:
            if content[j] == '{':
                depth += 1
            elif content[j] == '}':
                depth -= 1
                if depth == 0:
                    break
            j += 1

        if depth != 0:
            errors.append(f"Unbalanced braces for entry starting near position {at_pos}; stopped parsing.")
            break

        body = content[brace_start + 1:j]
        i = j + 1

        if entry_type in ('comment', 'string', 'preamble'):
            continue

        try:
            entry = _parse_bib_entry_body(entry_type, body)
            if entry.get('key'):
                entries.append(entry)
            else:
                errors.append(f"Entry near position {at_pos} has no citation key; skipped.")
        except Exception as e:
            errors.append(f"Failed to parse entry near position {at_pos}: {e}")
            continue

    return entries, errors


def _parse_bib_entry_body(entry_type, body):
    """Parse the inner body of a bib entry (everything between the outer braces)."""
    comma_idx = body.find(',')
    if comma_idx == -1:
        key = body.strip()
        fields_str = ""
    else:
        key = body[:comma_idx].strip()
        fields_str = body[comma_idx + 1:]

    fields = {}
    pos = 0
    L = len(fields_str)

    while pos < L:
        while pos < L and fields_str[pos] in ' \t\r\n,':
            pos += 1
        if pos >= L:
            break

        eq_idx = fields_str.find('=', pos)
        if eq_idx == -1:
            break
        field_name = fields_str[pos:eq_idx].strip().lower()
        pos = eq_idx + 1
        while pos < L and fields_str[pos] in ' \t\r\n':
            pos += 1
        if pos >= L:
            break

        if fields_str[pos] == '{':
            depth = 0
            start = pos
            while pos < L:
                if fields_str[pos] == '{':
                    depth += 1
                elif fields_str[pos] == '}':
                    depth -= 1
                    if depth == 0:
                        pos += 1
                        break
                pos += 1
            value = fields_str[start + 1:pos - 1] if pos - 1 > start else ""
        elif fields_str[pos] == '"':
            pos += 1
            start = pos
            while pos < L and fields_str[pos] != '"':
                pos += 1
            value = fields_str[start:pos]
            pos += 1
        else:
            start = pos
            while pos < L and fields_str[pos] != ',':
                pos += 1
            value = fields_str[start:pos].strip()

        if field_name:
            fields[field_name] = _clean_bib_value(value)

    return {'key': key, 'type': entry_type, 'fields': fields}


def _clean_bib_value(value):
    value = value.strip()
    value = re.sub(r'\s+', ' ', value)
    return value


def normalize_doi(doi):
    """Normalise a DOI string: strip URL prefixes, 'doi:' prefix, whitespace,
    surrounding punctuation and lower-case it."""
    if not doi:
        return ""
    doi = str(doi).strip()
    doi = re.sub(r'^https?://(dx\.)?doi\.org/', '', doi, flags=re.IGNORECASE)
    doi = re.sub(r'^doi\s*:\s*', '', doi, flags=re.IGNORECASE)
    doi = doi.strip().strip('{}').strip()
    doi = doi.strip('.,;: \t')
    return doi.lower()


def normalize_title(title):
    """Normalise title text for comparison: lower-case, strip LaTeX markup and
    punctuation, collapse whitespace."""
    if not title:
        return ""
    t = str(title).lower()
    t = re.sub(r'[{}\\]', '', t)
    t = re.sub(r'[^a-z0-9\s]', ' ', t)
    t = re.sub(r'\s+', ' ', t).strip()
    return t


def title_similarity(a, b):
    na, nb = normalize_title(a), normalize_title(b)
    if not na or not nb:
        return 0.0
    return SequenceMatcher(None, na, nb).ratio()


def extract_surnames(author_field):
    """Extract a set of normalised author surnames from a BibTeX 'author'
    string ("Last, First and Last2, First2") or a list of plain names."""
    surnames = set()
    if not author_field:
        return surnames

    if isinstance(author_field, list):
        names = author_field
    else:
        names = re.split(r'\s+and\s+', str(author_field), flags=re.IGNORECASE)

    for name in names:
        name = name.strip()
        if not name:
            continue
        if ',' in name:
            surname = name.split(',')[0].strip()
        else:
            parts = name.split()
            surname = parts[-1] if parts else name
        surname = re.sub(r'[^a-zA-Z\-]', '', surname).lower()
        if surname:
            surnames.add(surname)
    return surnames


def author_similarity(bib_authors, record_authors):
    set1 = extract_surnames(bib_authors)
    set2 = extract_surnames(record_authors)
    if not set1 or not set2:
        return 0.0
    overlap = set1 & set2
    return len(overlap) / max(len(set1), len(set2))


def build_bib_session():
    """Create a requests Session with retry/backoff for transient failures."""
    session = requests.Session()
    retries = Retry(
        total=3,
        backoff_factor=1.0,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=frozenset(['GET'])
    )
    adapter = HTTPAdapter(max_retries=retries)
    session.mount('https://', adapter)
    session.mount('http://', adapter)
    return session


def _http_get(session, url, params=None, timeout=15, headers=None):
    """Perform a GET request, returning (status_code, json_or_None, error_message).
    status_code is None only when the request itself failed (network error)."""
    try:
        r = session.get(url, params=params, timeout=timeout, headers=headers)
        if r.status_code == 200:
            try:
                return 200, r.json(), None
            except Exception as e:
                return r.status_code, None, f"Invalid JSON response: {e}"
        elif r.status_code == 404:
            return 404, None, None
        else:
            return r.status_code, None, f"HTTP {r.status_code}"
    except requests.exceptions.RequestException as e:
        return None, None, str(e)


def crossref_to_record(item):
    """Normalise a Crossref 'message' item into a common record dict."""
    title_list = item.get('title') or []
    title = title_list[0] if title_list else ""

    authors = []
    for a in item.get('author', []) or []:
        given = a.get('given', '') or ''
        family = a.get('family', '') or ''
        name = f"{given} {family}".strip()
        if name:
            authors.append(name)

    year = None
    for date_field in ('published-print', 'published-online', 'issued', 'created'):
        date_parts = (item.get(date_field) or {}).get('date-parts')
        if date_parts and date_parts[0]:
            year = date_parts[0][0]
            break

    journal_list = item.get('container-title') or []
    journal = journal_list[0] if journal_list else ""

    doi = normalize_doi(item.get('DOI', ''))

    return {
        'source': 'crossref',
        'title': title,
        'authors': authors,
        'year': year,
        'journal': journal,
        'volume': item.get('volume', '') or '',
        'issue': item.get('issue', '') or '',
        'pages': item.get('page', '') or '',
        'doi': doi,
        'url': item.get('URL', '') or (f"https://doi.org/{doi}" if doi else ""),
        'publisher': item.get('publisher', '') or '',
        'type': item.get('type', '') or ''
    }


def openalex_to_record(work):
    """Normalise an OpenAlex 'work' object into a common record dict."""
    title = work.get('title') or work.get('display_name') or ""

    authors = []
    for a in work.get('authorships', []) or []:
        name = a.get('author', {}).get('display_name', '') if a.get('author') else ''
        if name:
            authors.append(name)

    primary_location = work.get('primary_location') or {}
    source = primary_location.get('source') if isinstance(primary_location, dict) else None
    journal = source.get('display_name', '') if isinstance(source, dict) else ''

    biblio = work.get('biblio', {}) or {}
    first_page = biblio.get('first_page', '') or ''
    last_page = biblio.get('last_page', '') or ''
    pages = f"{first_page}--{last_page}" if first_page and last_page else (first_page or '')

    doi = normalize_doi(work.get('doi', '') or '')

    return {
        'source': 'openalex',
        'title': title,
        'authors': authors,
        'year': work.get('publication_year'),
        'journal': journal,
        'volume': biblio.get('volume', '') or '',
        'issue': biblio.get('issue', '') or '',
        'pages': pages,
        'doi': doi,
        'url': work.get('id', '') or (f"https://doi.org/{doi}" if doi else ""),
        'publisher': '',
        'type': work.get('type', '') or ''
    }


def lookup_doi(doi, session, timeout=15):
    """Look up a DOI via Crossref, falling back to OpenAlex.
    Returns (status, record, error) where status is 'found' | 'not_found' | 'error'."""
    cr_status, cr_data, cr_err = _http_get(
        session, f"https://api.crossref.org/works/{doi}", timeout=timeout, headers=CROSSREF_HEADERS
    )
    if cr_status == 200 and cr_data and cr_data.get('message'):
        return 'found', crossref_to_record(cr_data['message']), None

    oa_status, oa_data, oa_err = _http_get(
        session, f"https://api.openalex.org/works/https://doi.org/{doi}", timeout=timeout
    )
    if oa_status == 200 and oa_data:
        return 'found', openalex_to_record(oa_data), None

    if cr_status == 404 and oa_status == 404:
        return 'not_found', None, None

    if cr_status is None and oa_status is None:
        return 'error', None, cr_err or oa_err or "Unknown network error"

    # One source errored but the other gave a definitive 404 -> treat as not found,
    # but note the partial error for transparency.
    if cr_status == 404 or oa_status == 404:
        return 'not_found', None, None

    return 'error', None, cr_err or oa_err or "Unknown error contacting Crossref/OpenAlex"


def lookup_title(title, session, timeout=15):
    """Search Crossref (then OpenAlex) for a title. Returns (status, record, error)."""
    cr_status, cr_data, cr_err = _http_get(
        session, "https://api.crossref.org/works",
        params={"query.bibliographic": title, "rows": 3}, timeout=timeout, headers=CROSSREF_HEADERS
    )
    if cr_status == 200 and cr_data:
        items = (cr_data.get('message', {}) or {}).get('items', [])
        if items:
            return 'found', crossref_to_record(items[0]), None

    oa_status, oa_data, oa_err = _http_get(
        session, "https://api.openalex.org/works",
        params={"search": title, "per-page": 3}, timeout=timeout
    )
    if oa_status == 200 and oa_data:
        results = oa_data.get('results', [])
        if results:
            return 'found', openalex_to_record(results[0]), None

    if cr_status is None and oa_status is None:
        return 'error', None, cr_err or oa_err or "Unknown network error"

    if cr_status in (200, 404) or oa_status in (200, 404):
        return 'not_found', None, None

    return 'error', None, cr_err or oa_err or "Unknown error contacting Crossref/OpenAlex"


def compare_metadata(bib_entry, record):
    """Compare a parsed BibTeX entry against an authoritative record.
    Returns a dict with per-field scores, an overall confidence and a
    human-readable explanation (transparent scoring, not a black box)."""
    fields = bib_entry.get('fields', {})
    bib_title = fields.get('title', '')
    bib_authors = fields.get('author', '')
    bib_year = fields.get('year', '')
    bib_journal = fields.get('journal', '') or fields.get('booktitle', '')
    bib_doi = normalize_doi(fields.get('doi', ''))

    rec_title = record.get('title', '') if record else ''
    rec_authors = record.get('authors', []) if record else []
    rec_year = record.get('year') if record else None
    rec_journal = record.get('journal', '') if record else ''
    rec_doi = record.get('doi', '') if record else ''

    title_sim = title_similarity(bib_title, rec_title) if bib_title and rec_title else 0.0
    author_sim = author_similarity(bib_authors, rec_authors) if bib_authors and rec_authors else 0.0

    year_match = False
    try:
        if bib_year and rec_year:
            year_match = abs(int(str(bib_year)[:4]) - int(str(rec_year)[:4])) <= 1
    except (ValueError, TypeError):
        year_match = False

    journal_sim = title_similarity(bib_journal, rec_journal) if bib_journal and rec_journal else 0.0
    journal_match = journal_sim >= 0.6

    doi_match = bool(bib_doi) and bool(rec_doi) and bib_doi == rec_doi

    weights = {'title': 0.45, 'author': 0.25, 'year': 0.10, 'journal': 0.10, 'doi': 0.10}
    confidence = (
        weights['title'] * title_sim +
        weights['author'] * author_sim +
        weights['year'] * (1.0 if year_match else 0.0) +
        weights['journal'] * (1.0 if journal_match else 0.0) +
        weights['doi'] * (1.0 if doi_match else 0.0)
    )

    parts = [f"title similarity = {title_sim * 100:.0f}%"]
    if bib_authors and rec_authors:
        parts.append(f"author overlap = {author_sim * 100:.0f}%")
    if bib_year and rec_year:
        parts.append(f"year {'matches' if year_match else 'differs'} (bib={bib_year}, record={rec_year})")
    if bib_journal and rec_journal:
        parts.append(f"journal {'matches' if journal_match else 'differs'}")
    if bib_doi:
        parts.append(f"DOI {'matches' if doi_match else 'differs'}")

    return {
        'title_similarity': title_sim,
        'author_similarity': author_sim,
        'year_match': year_match,
        'journal_match': journal_match,
        'doi_match': doi_match,
        'confidence': confidence,
        'explanation': "; ".join(parts)
    }


def _cached_title_lookup(title, session, cache, timeout):
    norm = normalize_title(title)
    cache_key = f"title:{norm}"
    if cache_key in cache:
        return cache[cache_key]
    result = lookup_title(title, session, timeout=timeout)
    cache[cache_key] = result
    return result


def validate_bib_entry(entry, session, cache, timeout=15):
    """Validate a single BibTeX entry against Crossref/OpenAlex.
    Uses conservative matching: a DOI resolving is not sufficient on its own,
    metadata must also line up. Network/API failures yield UNVERIFIED, never
    a 'fake' verdict."""
    fields = entry.get('fields', {})
    title = (fields.get('title', '') or '').strip()
    doi = normalize_doi(fields.get('doi', ''))

    result = {
        'key': entry.get('key', ''),
        'type': entry.get('type', ''),
        'fields': fields,
        'status': 'UNVERIFIED',
        'confidence': 0.0,
        'comparison': None,
        'matched_record': None,
        'explanation': '',
    }

    if not title and not doi:
        result['status'] = 'INSUFFICIENT DATA'
        result['explanation'] = "Entry has no title and no DOI; cannot verify."
        return result

    if doi:
        cache_key = f"doi:{doi}"
        if cache_key in cache:
            doi_status, record, err = cache[cache_key]
        else:
            doi_status, record, err = lookup_doi(doi, session, timeout=timeout)
            cache[cache_key] = (doi_status, record, err)

        if doi_status == 'found' and record:
            comparison = compare_metadata(entry, record)
            result['comparison'] = comparison
            result['matched_record'] = record
            result['confidence'] = comparison['confidence']

            if comparison['title_similarity'] < BIB_TITLE_MISMATCH:
                result['status'] = 'DOI VALID - metadata mismatch'
                result['explanation'] = (
                    f"DOI resolves, but title similarity = {comparison['title_similarity'] * 100:.0f}%; "
                    f"possible incorrect DOI. {comparison['explanation']}"
                )
            elif comparison['confidence'] >= BIB_CONF_VALID and comparison['title_similarity'] >= BIB_TITLE_STRONG:
                result['status'] = 'VALID'
                result['explanation'] = comparison['explanation']
            elif comparison['confidence'] >= BIB_CONF_WEAK:
                result['status'] = 'VALID - minor metadata difference'
                result['explanation'] = comparison['explanation']
            else:
                result['status'] = 'SUSPICIOUS'
                result['explanation'] = "DOI resolves but overall metadata match is weak. " + comparison['explanation']
            return result

        elif doi_status == 'not_found':
            if title:
                t_status, t_record, t_err = _cached_title_lookup(title, session, cache, timeout)
                if t_status == 'found' and t_record:
                    comparison = compare_metadata(entry, t_record)
                    result['comparison'] = comparison
                    result['matched_record'] = t_record
                    result['confidence'] = comparison['confidence']
                    result['status'] = 'DOI NOT FOUND'
                    if comparison['title_similarity'] >= BIB_TITLE_STRONG:
                        result['explanation'] = (
                            f"DOI '{doi}' does not exist in Crossref/OpenAlex, but a matching paper was found "
                            f"by title (similarity {comparison['title_similarity'] * 100:.0f}%). "
                            f"Suggested DOI: {t_record.get('doi', 'N/A')}"
                        )
                    else:
                        result['explanation'] = (
                            f"DOI '{doi}' does not exist in Crossref/OpenAlex and no strong title match was found."
                        )
                    return result
                elif t_status == 'error':
                    result['status'] = 'DOI NOT FOUND'
                    result['explanation'] = f"DOI '{doi}' does not exist. Additional title search failed: {t_err}"
                    return result
            result['status'] = 'DOI NOT FOUND'
            result['explanation'] = f"DOI '{doi}' does not exist in Crossref or OpenAlex."
            return result

        else:  # error
            result['status'] = 'UNVERIFIED'
            result['explanation'] = f"Could not verify DOI due to a network/API error: {err}"
            return result

    # No DOI provided - try to find the paper by title
    if title:
        t_status, t_record, t_err = _cached_title_lookup(title, session, cache, timeout)
        if t_status == 'found' and t_record:
            comparison = compare_metadata(entry, t_record)
            result['comparison'] = comparison
            result['matched_record'] = t_record
            result['confidence'] = comparison['confidence']

            if comparison['confidence'] >= BIB_CONF_MINOR and comparison['title_similarity'] >= BIB_TITLE_STRONG:
                result['status'] = 'VALID - minor metadata difference'
                result['explanation'] = "No DOI in original entry; matching record found. " + comparison['explanation']
            elif comparison['confidence'] >= BIB_CONF_WEAK:
                result['status'] = 'SUSPICIOUS'
                result['explanation'] = "Weak match to an existing record; please verify manually. " + comparison['explanation']
            else:
                result['status'] = 'PAPER NOT FOUND'
                result['explanation'] = "No matching paper found in Crossref/OpenAlex for this title."
            return result
        elif t_status == 'not_found':
            result['status'] = 'PAPER NOT FOUND'
            result['explanation'] = "No matching paper found in Crossref/OpenAlex."
            return result
        else:
            result['status'] = 'UNVERIFIED'
            result['explanation'] = f"Could not verify title due to a network/API error: {t_err}"
            return result

    result['status'] = 'INSUFFICIENT DATA'
    result['explanation'] = "No DOI and no usable title to verify."
    return result


def detect_duplicates(entries):
    """Detect probable duplicate entries by DOI, normalised title, or
    title+year+first-author. Returns (group_of_index, groups_dict)."""
    doi_map = {}
    title_map = {}
    tya_map = {}
    group_of = {}
    groups = {}
    next_id = [0]

    def union(idx1, idx2):
        g1 = group_of.get(idx1)
        g2 = group_of.get(idx2)
        if g1 is not None and g2 is not None:
            if g1 != g2:
                for idx in groups[g2]:
                    group_of[idx] = g1
                groups[g1].extend(groups[g2])
                del groups[g2]
        elif g1 is not None:
            groups[g1].append(idx2)
            group_of[idx2] = g1
        elif g2 is not None:
            groups[g2].append(idx1)
            group_of[idx1] = g2
        else:
            gid = next_id[0]
            next_id[0] += 1
            groups[gid] = [idx1, idx2]
            group_of[idx1] = gid
            group_of[idx2] = gid

    for idx, entry in enumerate(entries):
        fields = entry.get('fields', {})
        doi = normalize_doi(fields.get('doi', ''))
        title_norm = normalize_title(fields.get('title', ''))
        year = fields.get('year', '')
        authors = extract_surnames(fields.get('author', ''))
        first_author = sorted(authors)[0] if authors else ''
        tya_key = f"{title_norm}|{year}|{first_author}" if title_norm else None

        if doi:
            if doi in doi_map:
                union(doi_map[doi], idx)
            else:
                doi_map[doi] = idx

        if title_norm and len(title_norm) > 8:
            if title_norm in title_map:
                union(title_map[title_norm], idx)
            else:
                title_map[title_norm] = idx

        if tya_key:
            if tya_key in tya_map:
                union(tya_map[tya_key], idx)
            else:
                tya_map[tya_key] = idx

    return group_of, groups


def validate_bib_file(entries, progress_callback=None, log_callback=None, should_stop=None,
                       cache=None, rate_limit_delay=0.3, timeout=15):
    """Validate a full list of parsed BibTeX entries sequentially (rate-limited),
    folding in duplicate detection. Returns a list of result dicts aligned with
    `entries` (same order/length)."""
    if cache is None:
        cache = {}
    session = build_bib_session()

    group_of, groups = detect_duplicates(entries)
    seen_groups = set()
    results = []
    total = len(entries)

    for idx, entry in enumerate(entries):
        if should_stop and should_stop():
            break

        key = entry.get('key', f'entry{idx}')
        if log_callback:
            log_callback(f"🔎 Validating [{idx + 1}/{total}]: {key}")

        try:
            result = validate_bib_entry(entry, session, cache, timeout=timeout)
        except Exception as e:
            result = {
                'key': key, 'type': entry.get('type', ''), 'fields': entry.get('fields', {}),
                'status': 'UNVERIFIED', 'confidence': 0.0, 'comparison': None,
                'matched_record': None, 'explanation': f"Unexpected error during validation: {e}"
            }

        gid = group_of.get(idx)
        result['duplicate_group'] = gid
        if gid is not None:
            members = groups.get(gid, [])
            if gid in seen_groups:
                result['status'] = 'POSSIBLE DUPLICATE'
                suffix = "ies" if len(members) > 2 else "y"
                result['explanation'] = (
                    f"Possible duplicate of {len(members) - 1} other entr{suffix} "
                    f"(matched by DOI/title). {result.get('explanation', '')}"
                )
            else:
                seen_groups.add(gid)

        results.append(result)

        if progress_callback:
            progress_callback(idx + 1, total, result)

        if rate_limit_delay:
            time.sleep(rate_limit_delay)

    return results


def fix_bib_entry(entry, matched_record):
    """Produce a corrected copy of a BibTeX entry's fields using an authoritative
    record, preserving the original citation key."""
    fields = dict(entry.get('fields', {}))
    if not matched_record:
        return {'key': entry.get('key'), 'type': entry.get('type'), 'fields': fields}

    if matched_record.get('title'):
        fields['title'] = matched_record['title']

    if matched_record.get('authors'):
        authors_bib = []
        for name in matched_record['authors']:
            parts = name.split()
            if len(parts) > 1:
                last = parts[-1]
                initials = " ".join(parts[:-1])
                authors_bib.append(f"{last}, {initials}")
            else:
                authors_bib.append(name)
        fields['author'] = " and ".join(authors_bib)

    if matched_record.get('year'):
        fields['year'] = str(matched_record['year'])
    if matched_record.get('journal'):
        fields['journal'] = matched_record['journal']
    if matched_record.get('volume'):
        fields['volume'] = str(matched_record['volume'])
    if matched_record.get('issue'):
        fields['number'] = str(matched_record['issue'])
    if matched_record.get('pages'):
        fields['pages'] = matched_record['pages']
    if matched_record.get('doi'):
        fields['doi'] = matched_record['doi']
    if matched_record.get('url'):
        fields['url'] = matched_record['url']
    if matched_record.get('publisher'):
        fields['publisher'] = matched_record['publisher']

    return {'key': entry.get('key'), 'type': entry.get('type'), 'fields': fields}


def remove_bib_entries(entries, keys_to_remove):
    """Return a new list of entries excluding those whose key is in keys_to_remove."""
    keys_to_remove = set(keys_to_remove)
    return [e for e in entries if e.get('key') not in keys_to_remove]


def bib_entry_to_string(entry):
    """Serialise an entry dict back to a BibTeX entry string."""
    key = entry.get('key', 'unknown')
    entry_type = entry.get('type', 'misc')
    fields = entry.get('fields', {})

    ordered_keys = [k for k in BIB_FIELD_ORDER if fields.get(k)]
    ordered_keys += [k for k in fields if k not in BIB_FIELD_ORDER and fields.get(k)]

    body_lines = [f"  {k:<10}= {{{fields[k]}}}" for k in ordered_keys]
    body = ",\n".join(body_lines)
    return f"@{entry_type}{{{key},\n{body}\n}}"


def save_validated_bib(entries, output_path):
    """Write a list of entry dicts to a new .bib file. Never overwrites the
    original source file (caller is responsible for choosing a new path)."""
    with open(output_path, 'w', encoding='utf8') as f:
        for entry in entries:
            f.write(bib_entry_to_string(entry))
            f.write("\n\n")
    return output_path


def is_result_fixable(result):
    """Conservative check: only offer automatic correction when we have a
    reliable authoritative record that genuinely corresponds to this entry
    (never for DOI/metadata mismatches, where the resolved record may be the
    WRONG paper)."""
    if not result or not result.get('matched_record'):
        return False
    status = result.get('status')
    comparison = result.get('comparison') or {}
    if status in ('VALID', 'VALID - minor metadata difference'):
        return True
    if status == 'DOI NOT FOUND' and comparison.get('title_similarity', 0) >= BIB_TITLE_STRONG:
        return True
    return False


def recommend_bib_action(status):
    mapping = {
        'VALID': 'Keep',
        'VALID - minor metadata difference': 'Keep (review minor differences)',
        'DOI VALID - metadata mismatch': 'Review - possible incorrect DOI',
        'DOI NOT FOUND': 'Review / fix DOI',
        'PAPER NOT FOUND': 'Review - could not verify',
        'POSSIBLE DUPLICATE': 'Review - choose one entry to keep',
        'INSUFFICIENT DATA': 'Add more metadata',
        'SUSPICIOUS': 'Review - manually verify',
        'UNVERIFIED': 'Retry validation later',
        'PENDING': 'Validation not yet run',
    }
    return mapping.get(status, 'Review')


def export_validation_report(results, output_path):
    """Export validation results to CSV or XLSX (based on output_path extension)."""
    rows = []
    for r in results:
        fields = r.get('fields', {})
        matched = r.get('matched_record') or {}
        comparison = r.get('comparison') or {}
        rows.append({
            'BibTeX Key': r.get('key', ''),
            'Original Title': fields.get('title', ''),
            'Corrected Title': matched.get('title', ''),
            'Original DOI': fields.get('doi', ''),
            'Verified DOI': matched.get('doi', ''),
            'Status': r.get('status', ''),
            'Title Similarity': f"{comparison.get('title_similarity', 0) * 100:.0f}%" if comparison else '',
            'Author Match': f"{comparison.get('author_similarity', 0) * 100:.0f}%" if comparison else '',
            'Year Match': comparison.get('year_match', '') if comparison else '',
            'Journal Match': comparison.get('journal_match', '') if comparison else '',
            'Confidence': f"{r.get('confidence', 0) * 100:.0f}%",
            'Explanation': r.get('explanation', ''),
            'Recommended Action': recommend_bib_action(r.get('status', ''))
        })

    df = pd.DataFrame(rows)
    ext = os.path.splitext(output_path)[1].lower()
    if ext == '.xlsx':
        df.to_excel(output_path, index=False, engine='openpyxl')
    else:
        df.to_csv(output_path, index=False, encoding='utf-8-sig')
    return output_path


# ==========================================================
# Paper Explorer Window
# ==========================================================

class PaperCard(QFrame):
    """Modern card widget for displaying paper information with remove button"""
    
    remove_requested = pyqtSignal(object)  # Emits the paper object
    
    def __init__(self, paper, theme='light', parent=None):
        super().__init__(parent)
        self.paper = paper
        self.theme = theme
        self.setup_ui()
        self.apply_styling()
    
    def setup_ui(self):
        layout = QVBoxLayout()
        layout.setSpacing(8)
        layout.setContentsMargins(15, 15, 15, 15)
        
        # Title
        title = self.paper.get("title", "Untitled")
        title_label = QLabel(title)
        title_label.setWordWrap(True)
        title_label.setStyleSheet("font-size: 14px; font-weight: bold;")
        layout.addWidget(title_label)
        
        # Authors
        authorships = self.paper.get("authorships", [])
        authors = []
        for auth in authorships[:5]:
            if auth and isinstance(auth, dict):
                name = auth.get("author", {}).get("display_name", "")
                if name:
                    authors.append(name)
        author_str = ", ".join(authors)
        if len(authorships) > 5:
            author_str += f" et al."
        
        author_label = QLabel(f"👤 {author_str}" if author_str else "👤 Unknown")
        author_label.setStyleSheet("font-size: 12px;")
        author_label.setWordWrap(True)
        layout.addWidget(author_label)
        
        # Year, Journal, Citations
        info_layout = QHBoxLayout()
        info_layout.setSpacing(15)
        
        year = self.paper.get("publication_year", "N/A")
        year_label = QLabel(f"📅 {year}")
        year_label.setStyleSheet("font-size: 11px;")
        info_layout.addWidget(year_label)
        
        # Journal
        primary_location = self.paper.get("primary_location")
        source = primary_location.get("source") if primary_location and isinstance(primary_location, dict) else None
        journal = source.get("display_name", "") if source and isinstance(source, dict) else ""
        if journal:
            journal_label = QLabel(f"📄 {journal}")
            journal_label.setStyleSheet("font-size: 11px;")
            info_layout.addWidget(journal_label)
        
        info_layout.addStretch()
        
        citedby = self.paper.get("cited_by_count", 0)
        cited_label = QLabel(f"⭐ {citedby} citations")
        cited_label.setStyleSheet("font-size: 11px; font-weight: bold;")
        info_layout.addWidget(cited_label)
        
        layout.addLayout(info_layout)
        
        # Keywords
        keywords = extract_keywords_from_paper(self.paper)
        if keywords:
            keywords_text = " ".join([f"#{kw}" for kw in keywords[:4]])
            keywords_label = QLabel(keywords_text)
            keywords_label.setStyleSheet("font-size: 11px;")
            layout.addWidget(keywords_label)
        
        # Abstract (truncated)
        abstract = abstract_from_index(self.paper.get("abstract_inverted_index"))
        if abstract:
            abstract_preview = abstract[:150] + "..." if len(abstract) > 150 else abstract
            abstract_label = QLabel(abstract_preview)
            abstract_label.setWordWrap(True)
            abstract_label.setStyleSheet("font-size: 12px; padding-top: 5px;")
            layout.addWidget(abstract_label)
        
        # Action buttons
        button_layout = QHBoxLayout()
        button_layout.setSpacing(10)
        
        # DOI button
        doi = self.paper.get("doi", "")
        if doi:
            doi_btn = QPushButton("🔗 Open Paper")
            doi_btn.setObjectName("primary")
            doi_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            doi_btn.clicked.connect(lambda: self.open_doi(doi))
            button_layout.addWidget(doi_btn)
        
        # OpenAlex button
        openalex_id = self.paper.get("id", "")
        if openalex_id:
            oa_btn = QPushButton("📚 OpenAlex")
            oa_btn.setObjectName("secondary")
            oa_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            oa_btn.clicked.connect(lambda: self.open_openalex(openalex_id))
            button_layout.addWidget(oa_btn)
        
        # Remove button
        remove_btn = QPushButton("🗑️ Remove")
        remove_btn.setObjectName("danger")
        remove_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        remove_btn.setToolTip("Remove this paper from your collection")
        remove_btn.clicked.connect(self.request_remove)
        button_layout.addWidget(remove_btn)
        
        button_layout.addStretch()
        layout.addLayout(button_layout)
        
        self.setLayout(layout)
    
    def apply_styling(self):
        """Apply styling based on current theme"""
        if self.theme == 'dark':
            self.setStyleSheet("""
                PaperCard {
                    background-color: #313244;
                    border-radius: 10px;
                    border: 1px solid #45475a;
                    margin: 5px;
                }
                PaperCard:hover {
                    background-color: #45475a;
                    border: 1px solid #89b4fa;
                }
                QLabel {
                    color: #cdd6f4;
                }
                QPushButton#primary {
                    background-color: #89b4fa;
                    color: #1e1e2e;
                    border: none;
                    border-radius: 5px;
                    padding: 6px 12px;
                    font-size: 11px;
                    font-weight: bold;
                }
                QPushButton#primary:hover {
                    background-color: #74c7ec;
                }
                QPushButton#secondary {
                    background-color: #45475a;
                    color: #cdd6f4;
                    border: none;
                    border-radius: 5px;
                    padding: 6px 12px;
                    font-size: 11px;
                }
                QPushButton#secondary:hover {
                    background-color: #585b70;
                }
                QPushButton#danger {
                    background-color: #f38ba8;
                    color: #1e1e2e;
                    border: none;
                    border-radius: 5px;
                    padding: 6px 12px;
                    font-size: 11px;
                    font-weight: bold;
                }
                QPushButton#danger:hover {
                    background-color: #eba0ac;
                }
            """)
        else:  # Light theme
            self.setStyleSheet("""
                PaperCard {
                    background-color: #ffffff;
                    border-radius: 10px;
                    border: 1px solid #d4d4d9;
                    margin: 5px;
                }
                PaperCard:hover {
                    background-color: #f0f0f5;
                    border: 1px solid #1e66f5;
                }
                QLabel {
                    color: #1a1b26;
                }
                QPushButton#primary {
                    background-color: #1e66f5;
                    color: #ffffff;
                    border: none;
                    border-radius: 5px;
                    padding: 6px 12px;
                    font-size: 11px;
                    font-weight: bold;
                }
                QPushButton#primary:hover {
                    background-color: #1a5bdb;
                }
                QPushButton#secondary {
                    background-color: #e8e8ed;
                    color: #1a1b26;
                    border: none;
                    border-radius: 5px;
                    padding: 6px 12px;
                    font-size: 11px;
                }
                QPushButton#secondary:hover {
                    background-color: #d4d4d9;
                }
                QPushButton#danger {
                    background-color: #c62828;
                    color: #ffffff;
                    border: none;
                    border-radius: 5px;
                    padding: 6px 12px;
                    font-size: 11px;
                    font-weight: bold;
                }
                QPushButton#danger:hover {
                    background-color: #b71c1c;
                }
            """)
    
    def update_theme(self, theme):
        """Update the card theme"""
        self.theme = theme
        self.apply_styling()
    
    def request_remove(self):
        """Emit signal to request removal of this paper"""
        self.remove_requested.emit(self.paper)
    
    def open_doi(self, doi):
        """Open DOI link in browser"""
        if doi:
            doi = doi.replace("https://doi.org/", "")
            webbrowser.open(f"https://doi.org/{doi}")
    
    def open_openalex(self, openalex_id):
        """Open OpenAlex link in browser"""
        if openalex_id:
            webbrowser.open(openalex_id)


class PaperExplorerWindow(QMainWindow):
    """Modern paper explorer window with card-based layout and removal support"""
    
    def __init__(self, papers, parent=None):
        super().__init__(parent)
        self.papers = papers
        self.filtered_papers = papers.copy()
        self.current_theme = 'light'
        self.cards = []
        self.removed_papers = []
        self.setWindowTitle("📚 Paper Explorer")
        self.setMinimumSize(1400, 800)
        
        self.setup_ui()
        self.apply_theme()
        self.display_papers()
    
    def setup_ui(self):
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)
        main_layout.setSpacing(10)
        main_layout.setContentsMargins(15, 15, 15, 15)
        
        # Header
        header_layout = QHBoxLayout()
        
        title = QLabel("📚 Paper Explorer")
        title.setStyleSheet("font-size: 20px; font-weight: bold;")
        header_layout.addWidget(title)
        
        header_layout.addStretch()
        
        # Search bar
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("🔍 Search papers...")
        self.search_input.setMinimumWidth(300)
        self.search_input.textChanged.connect(self.filter_papers)
        header_layout.addWidget(self.search_input)
        
        # Theme toggle
        self.theme_toggle = QPushButton("🌙 Dark")
        self.theme_toggle.setObjectName("primary")
        self.theme_toggle.setFixedWidth(100)
        self.theme_toggle.clicked.connect(self.toggle_theme)
        header_layout.addWidget(self.theme_toggle)
        
        # Close button
        close_btn = QPushButton("✕ Close")
        close_btn.setObjectName("danger")
        close_btn.clicked.connect(self.close)
        header_layout.addWidget(close_btn)
        
        main_layout.addLayout(header_layout)
        
        # Stats bar with remove info
        self.stats_label = QLabel()
        self.stats_label.setStyleSheet("padding: 5px;")
        main_layout.addWidget(self.stats_label)
        
        # Scroll area for cards
        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setStyleSheet("border: none;")
        
        self.card_container = QWidget()
        self.card_container.setStyleSheet("background-color: transparent;")
        self.card_layout = QGridLayout(self.card_container)
        self.card_layout.setSpacing(15)
        self.card_layout.setContentsMargins(10, 10, 10, 10)
        
        scroll_area.setWidget(self.card_container)
        main_layout.addWidget(scroll_area)
        
        # Bottom action bar
        bottom_layout = QHBoxLayout()
        bottom_layout.addStretch()
        
        # Save changes button
        self.save_btn = QPushButton("💾 Save Changes")
        self.save_btn.setObjectName("success")
        self.save_btn.setMinimumWidth(150)
        self.save_btn.clicked.connect(self.save_changes)
        bottom_layout.addWidget(self.save_btn)
        
        main_layout.addLayout(bottom_layout)
    
    def apply_theme(self):
        """Apply the current theme to all elements"""
        if self.current_theme == 'dark':
            self.setStyleSheet("""
                QMainWindow {
                    background-color: #1e1e2e;
                }
                QLabel {
                    color: #cdd6f4;
                }
                QLineEdit {
                    background-color: #1a1b26;
                    color: #cdd6f4;
                    border: 1px solid #45475a;
                    border-radius: 6px;
                    padding: 8px;
                }
                QLineEdit:focus {
                    border: 2px solid #89b4fa;
                }
                QPushButton#primary {
                    background-color: #89b4fa;
                    color: #1e1e2e;
                    border: none;
                    border-radius: 6px;
                    padding: 8px 16px;
                    font-weight: bold;
                }
                QPushButton#primary:hover {
                    background-color: #74c7ec;
                }
                QPushButton#success {
                    background-color: #a6e3a1;
                    color: #1e1e2e;
                    border: none;
                    border-radius: 6px;
                    padding: 8px 16px;
                    font-weight: bold;
                }
                QPushButton#success:hover {
                    background-color: #94e2d5;
                }
                QPushButton#danger {
                    background-color: #f38ba8;
                    color: #1e1e2e;
                    border: none;
                    border-radius: 6px;
                    padding: 8px 16px;
                    font-weight: bold;
                }
                QPushButton#danger:hover {
                    background-color: #eba0ac;
                }
                QScrollBar:vertical {
                    background-color: #313244;
                    width: 12px;
                    border-radius: 6px;
                }
                QScrollBar::handle:vertical {
                    background-color: #45475a;
                    border-radius: 6px;
                    min-height: 20px;
                }
                QScrollBar::handle:vertical:hover {
                    background-color: #585b70;
                }
            """)
            self.theme_toggle.setText("☀️ Light")
        else:
            self.setStyleSheet("""
                QMainWindow {
                    background-color: #f5f5f7;
                }
                QLabel {
                    color: #1a1b26;
                }
                QLineEdit {
                    background-color: #ffffff;
                    color: #1a1b26;
                    border: 1px solid #c4c4c9;
                    border-radius: 6px;
                    padding: 8px;
                }
                QLineEdit:focus {
                    border: 2px solid #1e66f5;
                }
                QPushButton#primary {
                    background-color: #1e66f5;
                    color: #ffffff;
                    border: none;
                    border-radius: 6px;
                    padding: 8px 16px;
                    font-weight: bold;
                }
                QPushButton#primary:hover {
                    background-color: #1a5bdb;
                }
                QPushButton#success {
                    background-color: #2e7d32;
                    color: #ffffff;
                    border: none;
                    border-radius: 6px;
                    padding: 8px 16px;
                    font-weight: bold;
                }
                QPushButton#success:hover {
                    background-color: #1b5e20;
                }
                QPushButton#danger {
                    background-color: #c62828;
                    color: #ffffff;
                    border: none;
                    border-radius: 6px;
                    padding: 8px 16px;
                    font-weight: bold;
                }
                QPushButton#danger:hover {
                    background-color: #b71c1c;
                }
                QScrollBar:vertical {
                    background-color: #e8e8ed;
                    width: 12px;
                    border-radius: 6px;
                }
                QScrollBar::handle:vertical {
                    background-color: #c4c4c9;
                    border-radius: 6px;
                    min-height: 20px;
                }
                QScrollBar::handle:vertical:hover {
                    background-color: #a8a8b0;
                }
            """)
            self.theme_toggle.setText("🌙 Dark")
        
        for card in self.cards:
            card.update_theme(self.current_theme)
        
        self.update_stats()
    
    def toggle_theme(self):
        """Toggle between dark and light themes"""
        if self.current_theme == 'dark':
            self.current_theme = 'light'
        else:
            self.current_theme = 'dark'
        self.apply_theme()
    
    def update_stats(self):
        """Update the statistics label"""
        total = len(self.papers)
        shown = len(self.filtered_papers)
        removed = len(self.removed_papers)
        stats_text = f"📊 Showing {shown} of {total} papers"
        if removed > 0:
            stats_text += f" | 🗑️ Removed: {removed}"
        self.stats_label.setText(stats_text)
    
    def display_papers(self, papers=None):
        """Display papers in card layout"""
        for i in reversed(range(self.card_layout.count())):
            widget = self.card_layout.itemAt(i).widget()
            if widget:
                widget.setParent(None)
        self.cards = []
        
        if papers is None:
            papers = self.filtered_papers
        
        if not papers:
            empty_label = QLabel("No papers found matching your search.")
            empty_label.setStyleSheet("font-size: 16px; padding: 50px;")
            empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.card_layout.addWidget(empty_label, 0, 0)
        else:
            columns = 2
            row = 0
            col = 0
            
            for paper in papers:
                card = PaperCard(paper, theme=self.current_theme)
                card.remove_requested.connect(self.remove_paper)
                self.card_layout.addWidget(card, row, col)
                self.cards.append(card)
                
                col += 1
                if col >= columns:
                    col = 0
                    row += 1
        
        self.update_stats()
    
    def remove_paper(self, paper):
        """Remove a paper from the collection"""
        title = paper.get("title", "Untitled")
        reply = QMessageBox.question(
            self,
            "Remove Paper",
            f"Are you sure you want to remove:\n\n\"{title}\"\n\nThis will remove it from your collection.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        
        if reply == QMessageBox.StandardButton.Yes:
            if paper in self.papers:
                self.papers.remove(paper)
                self.removed_papers.append(paper)
            
            if paper in self.filtered_papers:
                self.filtered_papers.remove(paper)
            
            self.display_papers()
            
            if self.parent():
                self.parent().log_message(f"🗑️ Removed: {title[:60]}...")
    
    def filter_papers(self, text):
        """Filter papers based on search text"""
        if not text:
            self.filtered_papers = self.papers.copy()
            self.display_papers()
            return
        
        text = text.lower()
        filtered = []
        for paper in self.papers:
            title = paper.get("title", "").lower()
            
            authors = []
            for auth in paper.get("authorships", []):
                name = auth.get("author", {}).get("display_name", "")
                if name:
                    authors.append(name.lower())
            author_str = " ".join(authors)
            
            year = str(paper.get("publication_year", ""))
            
            primary_location = paper.get("primary_location")
            source = primary_location.get("source") if primary_location and isinstance(primary_location, dict) else None
            journal = source.get("display_name", "").lower() if source and isinstance(source, dict) else ""
            
            abstract = abstract_from_index(paper.get("abstract_inverted_index")).lower()
            keywords = " ".join(extract_keywords_from_paper(paper)).lower()
            
            search_text = f"{title} {author_str} {year} {journal} {abstract} {keywords}"
            
            if text in search_text:
                filtered.append(paper)
        
        self.filtered_papers = filtered
        self.display_papers()
    
    def save_changes(self):
        """Save the changes (regenerate files without removed papers)"""
        if not self.removed_papers:
            QMessageBox.information(self, "No Changes", 
                                   "No papers have been removed. Nothing to save.")
            return
        
        reply = QMessageBox.question(
            self,
            "Save Changes",
            f"You have removed {len(self.removed_papers)} papers.\n\n"
            "This will regenerate all output files without the removed papers.\n\n"
            "Do you want to continue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        
        if reply == QMessageBox.StandardButton.Yes:
            if self.parent() and hasattr(self.parent(), 'output_dir'):
                output_dir = self.parent().output_dir
                query = " ".join(self.parent().keywords_edit.toPlainText().strip().split('\n')) if self.parent() else "HVAC YOLO"
                
                files = save_all_outputs(
                    self.papers,
                    output_dir,
                    query,
                    self.parent().abstract_filter_checkbox.isChecked() if self.parent() else True,
                    self.parent().citation_filter_checkbox.isChecked() if self.parent() else False,
                    self.parent().journal_filter_checkbox.isChecked() if self.parent() else False
                )
                
                QMessageBox.information(
                    self,
                    "Success",
                    f"✅ Successfully regenerated {len(files)-3} files!\n\n"
                    f"Removed papers: {len(self.removed_papers)}\n"
                    f"Remaining papers: {len(self.papers)}\n\n"
                    f"All files updated in: {output_dir}"
                )
                
                self.removed_papers = []
                self.update_stats()
            else:
                QMessageBox.warning(self, "Error", 
                                   "Could not save changes. Parent window not found.")


# ==========================================================
# BibTeX Validator Window
# ==========================================================

BIB_STATUS_COLORS_LIGHT = {
    'VALID': '#2e7d32',
    'VALID - minor metadata difference': '#558b2f',
    'DOI VALID - metadata mismatch': '#f57c00',
    'DOI NOT FOUND': '#c62828',
    'PAPER NOT FOUND': '#c62828',
    'POSSIBLE DUPLICATE': '#6a1b9a',
    'INSUFFICIENT DATA': '#757575',
    'SUSPICIOUS': '#e65100',
    'UNVERIFIED': '#757575',
    'PENDING': '#9e9e9e',
}

BIB_STATUS_COLORS_DARK = {
    'VALID': '#a6e3a1',
    'VALID - minor metadata difference': '#94e2d5',
    'DOI VALID - metadata mismatch': '#f9e2af',
    'DOI NOT FOUND': '#f38ba8',
    'PAPER NOT FOUND': '#f38ba8',
    'POSSIBLE DUPLICATE': '#cba6f7',
    'INSUFFICIENT DATA': '#a6adc8',
    'SUSPICIOUS': '#fab387',
    'UNVERIFIED': '#a6adc8',
    'PENDING': '#6c7086',
}


class BibValidatorWindow(QMainWindow):
    """Window for validating a .bib file's references against Crossref/OpenAlex,
    reviewing results, fixing reliable metadata, removing invalid/duplicate
    entries and exporting a cleaned .bib file plus a validation report.

    The original .bib file is never modified; all output is written to new
    files chosen by the user.
    """

    COL_STATUS = 0
    COL_KEY = 1
    COL_TITLE = 2
    COL_DOI = 3
    COL_MATCH = 4
    COL_YEAR = 5
    COL_ACTION = 6

    def __init__(self, bib_path, entries, parent=None):
        super().__init__(parent)
        self.bib_path = bib_path
        self.original_entries = entries          # never mutated
        self.entries = [copy.deepcopy(e) for e in entries]
        self.results = [None] * len(self.entries)  # aligned with self.entries
        self.current_theme = getattr(parent, 'current_theme', 'light') if parent else 'light'
        self.threadpool = QThreadPool()
        self.worker = None

        self.setWindowTitle("🔎 BibTeX Reference Checker")
        self.setMinimumSize(1400, 800)

        self.setup_ui()
        self.apply_theme()
        self.populate_table()
        self.update_stats()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------
    def setup_ui(self):
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)
        main_layout.setSpacing(10)
        main_layout.setContentsMargins(15, 15, 15, 15)

        # Header
        header_layout = QHBoxLayout()
        title = QLabel(f"🔎 BibTeX Reference Checker — {os.path.basename(self.bib_path)}")
        title.setStyleSheet("font-size: 18px; font-weight: bold;")
        header_layout.addWidget(title)
        header_layout.addStretch()

        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("🔍 Search references...")
        self.search_input.setMinimumWidth(280)
        self.search_input.textChanged.connect(self.apply_filter)
        header_layout.addWidget(self.search_input)

        self.status_filter_combo = QComboBox()
        self.status_filter_combo.addItem("All statuses")
        self.status_filter_combo.addItems([
            'VALID', 'VALID - minor metadata difference', 'DOI VALID - metadata mismatch',
            'DOI NOT FOUND', 'PAPER NOT FOUND', 'POSSIBLE DUPLICATE',
            'INSUFFICIENT DATA', 'SUSPICIOUS', 'UNVERIFIED', 'PENDING'
        ])
        self.status_filter_combo.currentIndexChanged.connect(self.apply_filter)
        header_layout.addWidget(self.status_filter_combo)

        close_btn = QPushButton("✕ Close")
        close_btn.setObjectName("danger")
        close_btn.clicked.connect(self.close)
        header_layout.addWidget(close_btn)

        main_layout.addLayout(header_layout)

        # Stats bar
        self.stats_label = QLabel()
        self.stats_label.setStyleSheet("padding: 5px; font-size: 13px; font-weight: bold;")
        main_layout.addWidget(self.stats_label)

        # Progress bar + current-entry status
        progress_layout = QHBoxLayout()
        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        progress_layout.addWidget(self.progress_bar)
        main_layout.addLayout(progress_layout)

        self.current_status_label = QLabel("")
        self.current_status_label.setStyleSheet("font-size: 11px; padding: 2px;")
        main_layout.addWidget(self.current_status_label)

        # Table
        self.table = QTableWidget()
        self.table.setColumnCount(7)
        self.table.setHorizontalHeaderLabels(
            ["Status", "BibTeX Key", "Title", "DOI", "Match", "Year", "Explanation / Action"]
        )
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSortingEnabled(True)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.horizontalHeader().setSectionResizeMode(self.COL_TITLE, QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().setVisible(False)
        self.table.itemSelectionChanged.connect(self.on_selection_changed)
        main_layout.addWidget(self.table)

        # Detail panel for the selected entry
        self.detail_label = QLabel("Select a reference to see full details and explanation.")
        self.detail_label.setWordWrap(True)
        self.detail_label.setStyleSheet("padding: 8px; font-size: 12px;")
        self.detail_label.setMinimumHeight(60)
        main_layout.addWidget(self.detail_label)

        # Action buttons
        action_layout = QHBoxLayout()

        self.validate_btn = QPushButton("🚀 Start Validation")
        self.validate_btn.setObjectName("primary")
        self.validate_btn.clicked.connect(self.start_validation)
        action_layout.addWidget(self.validate_btn)

        self.stop_btn = QPushButton("⏹️ Stop")
        self.stop_btn.setObjectName("danger")
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self.stop_validation)
        action_layout.addWidget(self.stop_btn)

        self.fix_btn = QPushButton("🛠️ Fix References")
        self.fix_btn.setObjectName("success")
        self.fix_btn.clicked.connect(self.fix_references)
        action_layout.addWidget(self.fix_btn)

        self.remove_selected_btn = QPushButton("🗑️ Remove Selected")
        self.remove_selected_btn.clicked.connect(self.remove_selected)
        action_layout.addWidget(self.remove_selected_btn)

        self.remove_invalid_btn = QPushButton("🚫 Remove All Invalid")
        self.remove_invalid_btn.setObjectName("danger")
        self.remove_invalid_btn.clicked.connect(self.remove_all_invalid)
        action_layout.addWidget(self.remove_invalid_btn)

        action_layout.addStretch()

        self.export_report_btn = QPushButton("📊 Export Report")
        self.export_report_btn.clicked.connect(self.export_report)
        action_layout.addWidget(self.export_report_btn)

        self.save_bib_btn = QPushButton("💾 Save Validated BibTeX")
        self.save_bib_btn.setObjectName("success")
        self.save_bib_btn.clicked.connect(self.save_validated)
        action_layout.addWidget(self.save_bib_btn)

        main_layout.addLayout(action_layout)

        self.status_bar = self.statusBar()
        self.status_bar.showMessage(f"Loaded {len(self.entries)} references from {os.path.basename(self.bib_path)}")

    def apply_theme(self):
        if self.current_theme == 'dark':
            self.setStyleSheet("""
                QMainWindow { background-color: #1e1e2e; }
                QLabel { color: #cdd6f4; }
                QLineEdit, QComboBox {
                    background-color: #1a1b26; color: #cdd6f4;
                    border: 1px solid #45475a; border-radius: 6px; padding: 6px;
                }
                QTableWidget {
                    background-color: #181825; color: #cdd6f4;
                    gridline-color: #45475a; border: 1px solid #45475a;
                }
                QHeaderView::section {
                    background-color: #313244; color: #cdd6f4; padding: 6px; border: none;
                }
                QPushButton {
                    background-color: #45475a; color: #cdd6f4; border: none;
                    border-radius: 6px; padding: 8px 14px; font-weight: bold;
                }
                QPushButton:hover { background-color: #585b70; }
                QPushButton#primary { background-color: #89b4fa; color: #1e1e2e; }
                QPushButton#success { background-color: #a6e3a1; color: #1e1e2e; }
                QPushButton#danger { background-color: #f38ba8; color: #1e1e2e; }
                QProgressBar {
                    border: 1px solid #45475a; border-radius: 6px; text-align: center;
                    color: #cdd6f4; background-color: #313244;
                }
                QProgressBar::chunk { background-color: #89b4fa; border-radius: 6px; }
            """)
        else:
            self.setStyleSheet("""
                QMainWindow { background-color: #f5f5f7; }
                QLabel { color: #1a1b26; }
                QLineEdit, QComboBox {
                    background-color: #ffffff; color: #1a1b26;
                    border: 1px solid #c4c4c9; border-radius: 6px; padding: 6px;
                }
                QTableWidget {
                    background-color: #ffffff; color: #1a1b26;
                    gridline-color: #e0e0e5; border: 1px solid #c4c4c9;
                }
                QHeaderView::section {
                    background-color: #e8e8ed; color: #1a1b26; padding: 6px; border: none;
                }
                QPushButton {
                    background-color: #e8e8ed; color: #1a1b26; border: none;
                    border-radius: 6px; padding: 8px 14px; font-weight: bold;
                }
                QPushButton:hover { background-color: #d4d4d9; }
                QPushButton#primary { background-color: #1e66f5; color: #ffffff; }
                QPushButton#success { background-color: #2e7d32; color: #ffffff; }
                QPushButton#danger { background-color: #c62828; color: #ffffff; }
                QProgressBar {
                    border: 1px solid #c4c4c9; border-radius: 6px; text-align: center;
                    color: #1a1b26; background-color: #e8e8ed;
                }
                QProgressBar::chunk { background-color: #1e66f5; border-radius: 6px; }
            """)

    def _status_color(self, status):
        palette = BIB_STATUS_COLORS_DARK if self.current_theme == 'dark' else BIB_STATUS_COLORS_LIGHT
        return QColor(palette.get(status, '#9e9e9e'))

    # ------------------------------------------------------------------
    # Table population
    # ------------------------------------------------------------------
    def populate_table(self):
        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(self.entries))
        for row, entry in enumerate(self.entries):
            self.refresh_row(row)
        self.table.setSortingEnabled(True)

    def refresh_row(self, row):
        entry = self.entries[row]
        result = self.results[row]
        fields = entry.get('fields', {})

        status = result['status'] if result else 'PENDING'
        confidence = result.get('confidence', 0.0) if result else 0.0
        explanation = result.get('explanation', '') if result else 'Not yet validated.'

        status_item = QTableWidgetItem(status)
        status_item.setForeground(self._status_color(status))
        font = status_item.font()
        font.setBold(True)
        status_item.setFont(font)
        status_item.setData(Qt.ItemDataRole.UserRole, row)

        key_item = QTableWidgetItem(entry.get('key', ''))
        title_item = QTableWidgetItem(fields.get('title', ''))

        doi_display = fields.get('doi', '') or '—'
        if status == 'DOI NOT FOUND':
            doi_display = f"{fields.get('doi', '') or '(none)'} — NOT FOUND"
        doi_item = QTableWidgetItem(doi_display)

        match_item = QTableWidgetItem(f"{confidence * 100:.0f}%")
        match_item.setData(Qt.ItemDataRole.EditRole, confidence)

        year_item = QTableWidgetItem(str(fields.get('year', '') or ''))

        action_item = QTableWidgetItem(explanation if result else '')

        for item in (status_item, key_item, title_item, doi_item, match_item, year_item, action_item):
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)

        self.table.setItem(row, self.COL_STATUS, status_item)
        self.table.setItem(row, self.COL_KEY, key_item)
        self.table.setItem(row, self.COL_TITLE, title_item)
        self.table.setItem(row, self.COL_DOI, doi_item)
        self.table.setItem(row, self.COL_MATCH, match_item)
        self.table.setItem(row, self.COL_YEAR, year_item)
        self.table.setItem(row, self.COL_ACTION, action_item)

    def apply_filter(self):
        text = self.search_input.text().strip().lower()
        status_filter = self.status_filter_combo.currentText()

        for row in range(self.table.rowCount()):
            entry = self.entries[row]
            fields = entry.get('fields', {})
            result = self.results[row]
            status = result['status'] if result else 'PENDING'

            matches_status = (status_filter == "All statuses" or status == status_filter)

            if text:
                haystack = " ".join([
                    entry.get('key', ''), fields.get('title', ''), fields.get('author', ''),
                    fields.get('doi', ''), fields.get('journal', ''), str(fields.get('year', ''))
                ]).lower()
                matches_text = text in haystack
            else:
                matches_text = True

            self.table.setRowHidden(row, not (matches_status and matches_text))

    def on_selection_changed(self):
        rows = sorted(set(idx.row() for idx in self.table.selectedIndexes()))
        if len(rows) != 1:
            if len(rows) > 1:
                self.detail_label.setText(f"{len(rows)} references selected.")
            return
        row = rows[0]
        if row >= len(self.entries):
            return
        entry = self.entries[row]
        result = self.results[row]
        fields = entry.get('fields', {})

        lines = [f"<b>{entry.get('key', '')}</b> — {fields.get('title', '(no title)')}"]
        lines.append(f"Authors: {fields.get('author', 'N/A')} | Year: {fields.get('year', 'N/A')} | "
                     f"Journal: {fields.get('journal', fields.get('booktitle', 'N/A'))}")
        lines.append(f"DOI: {fields.get('doi', 'N/A')} | URL: {fields.get('url', 'N/A')}")

        if result:
            lines.append(f"<br><b>Status:</b> {result['status']} (confidence {result.get('confidence', 0) * 100:.0f}%)")
            lines.append(f"<b>Explanation:</b> {result.get('explanation', '')}")
            matched = result.get('matched_record')
            if matched:
                lines.append(f"<b>Matched record ({matched.get('source')}):</b> {matched.get('title', '')} "
                              f"({matched.get('year', 'N/A')}) — DOI: {matched.get('doi', 'N/A')}")
        else:
            lines.append("<br><i>Not yet validated.</i>")

        self.detail_label.setText("<br>".join(lines))

    # ------------------------------------------------------------------
    # Stats
    # ------------------------------------------------------------------
    def update_stats(self):
        total = len(self.entries)
        valid = sum(1 for r in self.results if r and r['status'] in BIB_VALID_STATUSES)
        warnings = sum(1 for r in self.results if r and r['status'] in BIB_WARNING_STATUSES)
        invalid = sum(1 for r in self.results if r and r['status'] in BIB_INVALID_STATUSES)
        duplicates = sum(1 for r in self.results if r and r['status'] in BIB_DUPLICATE_STATUSES)
        unverified = sum(1 for r in self.results if r and r['status'] in BIB_UNVERIFIED_STATUSES)
        pending = sum(1 for r in self.results if not r)

        text = (f"📊 Total: {total}   |   ✅ Valid: {valid}   |   ⚠️ Warnings: {warnings}   |   "
                f"❌ Invalid: {invalid}   |   🧬 Duplicates: {duplicates}   |   "
                f"❓ Unverified: {unverified}")
        if pending:
            text += f"   |   ⏳ Pending: {pending}"
        self.stats_label.setText(text)

    # ------------------------------------------------------------------
    # Validation lifecycle
    # ------------------------------------------------------------------
    def start_validation(self):
        if not self.entries:
            QMessageBox.information(self, "No References", "There are no BibTeX entries to validate.")
            return

        self.validate_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.progress_bar.setVisible(True)
        self.progress_bar.setMaximum(len(self.entries))
        self.progress_bar.setValue(0)
        self.status_bar.showMessage("Validating references...")

        self.worker = BibValidationWorker(self.entries, rate_limit_delay=0.3, timeout=15)
        self.worker.signals.progress.connect(self.on_progress)
        self.worker.signals.entry_validated.connect(self.on_entry_validated)
        self.worker.signals.log.connect(self.on_log)
        self.worker.signals.error.connect(self.on_validation_error)
        self.worker.signals.finished.connect(self.on_validation_finished)
        self.threadpool.start(self.worker)

    def stop_validation(self):
        if self.worker:
            self.worker.stop()
            self.stop_btn.setEnabled(False)
            self.current_status_label.setText("Stopping after current reference...")

    def on_progress(self, current, total):
        self.progress_bar.setMaximum(total)
        self.progress_bar.setValue(current)

    def on_entry_validated(self, index, result):
        if 0 <= index < len(self.results):
            self.results[index] = result
            self.refresh_row(index)
            self.update_stats()
            key = self.entries[index].get('key', '')
            self.current_status_label.setText(f"Validated: {key} → {result['status']}")

    def on_log(self, message):
        self.status_bar.showMessage(message)

    def on_validation_error(self, message):
        self.validate_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self.progress_bar.setVisible(False)
        QMessageBox.critical(self, "Validation Error", message)

    def on_validation_finished(self, results):
        self.validate_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self.progress_bar.setVisible(False)
        self.status_bar.showMessage("Validation complete.")
        self.update_stats()
        self.apply_filter()

    # ------------------------------------------------------------------
    # Fix / remove / save / export
    # ------------------------------------------------------------------
    def _selected_rows(self):
        return sorted(set(idx.row() for idx in self.table.selectedIndexes()))

    def fix_references(self):
        """Apply automatic correction to entries with a reliable authoritative
        match. Never touches entries without a trustworthy match."""
        fixable_rows = [i for i, r in enumerate(self.results) if is_result_fixable(r)]
        if not fixable_rows:
            QMessageBox.information(
                self, "Nothing to Fix",
                "No references currently have a reliable authoritative match to fix.\n"
                "Run validation first, or review warnings/invalid entries manually."
            )
            return

        reply = QMessageBox.question(
            self, "Fix References",
            f"{len(fixable_rows)} reference(s) have a reliable match and can be "
            f"automatically corrected (title, authors, journal, year, volume, issue, "
            f"pages, DOI, URL, publisher).\n\nThis only changes the in-memory copy; "
            f"the original file is never modified. Continue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        for row in fixable_rows:
            fixed = fix_bib_entry(self.entries[row], self.results[row].get('matched_record'))
            self.entries[row] = fixed
            self.refresh_row(row)

        QMessageBox.information(self, "Fixed", f"Updated metadata for {len(fixable_rows)} reference(s).\n"
                                               f"Use 'Save Validated BibTeX' to write a new file.")

    def remove_selected(self):
        rows = self._selected_rows()
        if not rows:
            QMessageBox.information(self, "No Selection", "Select one or more rows to remove.")
            return

        keys = [self.entries[r].get('key', '') for r in rows]
        reply = QMessageBox.question(
            self, "Remove Selected",
            f"Remove {len(rows)} selected reference(s)?\n\n" + "\n".join(f"• {k}" for k in keys[:15]) +
            ("\n..." if len(keys) > 15 else ""),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        self._remove_rows(rows)

    def remove_all_invalid(self):
        invalid_rows = [i for i, r in enumerate(self.results)
                        if r and r['status'] in BIB_INVALID_STATUSES]
        if not invalid_rows:
            QMessageBox.information(self, "No Invalid References",
                                   "No references are currently marked INVALID "
                                   "(DOI NOT FOUND / PAPER NOT FOUND).")
            return

        keys = [self.entries[r].get('key', '') for r in invalid_rows]
        reply = QMessageBox.question(
            self, "Remove All Invalid",
            f"This will remove {len(invalid_rows)} reference(s) currently marked as invalid:\n\n" +
            "\n".join(f"• {k}" for k in keys[:15]) + ("\n..." if len(keys) > 15 else "") +
            "\n\nThis action cannot be undone within this session. Continue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        self._remove_rows(invalid_rows)

    def _remove_rows(self, rows):
        rows = sorted(set(rows), reverse=True)
        for row in rows:
            del self.entries[row]
            del self.results[row]
        self.populate_table()
        self.update_stats()
        self.apply_filter()
        self.status_bar.showMessage(f"Removed {len(rows)} reference(s). {len(self.entries)} remaining.")

    def save_validated(self):
        if not self.entries:
            QMessageBox.information(self, "Nothing to Save", "There are no references left to save.")
            return

        base_dir = os.path.dirname(self.bib_path) or os.getcwd()
        base_name = os.path.splitext(os.path.basename(self.bib_path))[0]
        default_path = os.path.join(base_dir, f"{base_name}_validated.bib")

        path, _ = QFileDialog.getSaveFileName(
            self, "Save Validated BibTeX", default_path, "BibTeX Files (*.bib)"
        )
        if not path:
            return
        if not path.lower().endswith('.bib'):
            path += '.bib'

        try:
            save_validated_bib(self.entries, path)
            QMessageBox.information(self, "Saved",
                                   f"✅ Saved {len(self.entries)} reference(s) to:\n{path}\n\n"
                                   f"The original file was not modified.")
            self.status_bar.showMessage(f"Saved validated BibTeX to {path}")
        except Exception as e:
            QMessageBox.critical(self, "Save Failed", f"Could not save file:\n{e}")

    def export_report(self):
        if not any(self.results):
            QMessageBox.information(self, "No Results", "Run validation before exporting a report.")
            return

        base_dir = os.path.dirname(self.bib_path) or os.getcwd()
        base_name = os.path.splitext(os.path.basename(self.bib_path))[0]
        default_path = os.path.join(base_dir, f"{base_name}_validation_report.csv")

        path, _ = QFileDialog.getSaveFileName(
            self, "Export Validation Report", default_path,
            "CSV Files (*.csv);;Excel Files (*.xlsx)"
        )
        if not path:
            return

        full_results = []
        for entry, result in zip(self.entries, self.results):
            if result:
                full_results.append(result)
            else:
                full_results.append({
                    'key': entry.get('key', ''), 'fields': entry.get('fields', {}),
                    'status': 'PENDING', 'confidence': 0.0, 'comparison': None,
                    'matched_record': None, 'explanation': 'Not yet validated.'
                })

        try:
            export_validation_report(full_results, path)
            QMessageBox.information(self, "Report Exported", f"✅ Validation report saved to:\n{path}")
        except Exception as e:
            QMessageBox.critical(self, "Export Failed", f"Could not export report:\n{e}")

    def closeEvent(self, event):
        if self.worker:
            self.worker.stop()
        event.accept()


# ==========================================================
# Main GUI Application
# ==========================================================

class WorkerSignals(QObject):
    progress = pyqtSignal(int, int)
    log = pyqtSignal(str)
    finished = pyqtSignal(dict)
    error = pyqtSignal(str)

class FetchWorker(QRunnable):
    def __init__(self, keywords, max_results, year_start, year_end, output_dir, only_with_abstract, only_with_citations=False, only_journal_papers=False):
        super().__init__()
        self.keywords = keywords
        self.max_results = max_results
        self.year_start = year_start
        self.year_end = year_end
        self.output_dir = output_dir
        self.only_with_abstract = only_with_abstract
        self.only_with_citations = only_with_citations
        self.only_journal_papers = only_journal_papers
        self.signals = WorkerSignals()
        self.is_running = True

    def run(self):
        try:
            query = " ".join(self.keywords)
            self.signals.log.emit(f"🔍 Searching: {query}")

            if self.year_start or self.year_end:
                year_range = f"{self.year_start or 'any'} - {self.year_end or 'any'}"
                self.signals.log.emit(f"📅 Year range: {year_range}")

            self.signals.log.emit(f"📊 Max results: {self.max_results}")
            if self.only_with_abstract:
                self.signals.log.emit(f"📝 Filter: Only papers with abstracts")
            if self.only_with_citations:
                self.signals.log.emit(f"⭐ Filter: Only papers with citations (sorted high to low)")
            if self.only_journal_papers:
                self.signals.log.emit(f"📰 Filter: Only journal papers (no thesis/conference)")
            self.signals.log.emit("-" * 50)
            
            def progress_callback(current, total, error=None):
                if error:
                    self.signals.error.emit(error)
                else:
                    self.signals.progress.emit(current, total)
                    if current % 5 == 0:
                        self.signals.log.emit(f"📄 Fetched {current} papers...")
            
            papers = fetch_papers_with_filters(
                query, 
                self.max_results, 
                self.year_start, 
                self.year_end,
                progress_callback
            )
            
            if not papers:
                self.signals.error.emit("No papers found!")
                return
            
            original_count = len(papers)
            self.signals.log.emit(f"\n✅ Found {original_count} papers")
            
            if self.only_with_abstract:
                papers = filter_papers_with_abstract(papers)
                filtered_count = len(papers)
                self.signals.log.emit(f"📝 Filtered to {filtered_count} papers with abstracts ({original_count - filtered_count} removed)")

            if self.only_journal_papers:
                before_journal_filter = len(papers)
                papers = filter_journal_papers(papers)
                self.signals.log.emit(f"📰 Filtered to {len(papers)} journal papers ({before_journal_filter - len(papers)} removed: thesis/conference/other)")

            if self.only_with_citations:
                before_citation_filter = len(papers)
                papers = filter_and_sort_by_citations(papers)
                self.signals.log.emit(f"⭐ Filtered to {len(papers)} papers with citations, sorted high to low ({before_citation_filter - len(papers)} removed)")

            self.signals.log.emit("📝 Generating output files...")

            files = save_all_outputs(papers, self.output_dir, query, self.only_with_abstract, self.only_with_citations, self.only_journal_papers)
            
            files['original_count'] = original_count
            files['filtered_count'] = len(papers)
            files['papers'] = papers
            
            self.signals.log.emit("\n📄 Generated files:")
            for key, path in files.items():
                if key not in ['original_count', 'filtered_count', 'papers']:
                    self.signals.log.emit(f"   • {os.path.basename(path)}")
            
            self.signals.finished.emit(files)
            
        except Exception as e:
            self.signals.error.emit(f"Error: {str(e)}")


class BibValidationSignals(QObject):
    """Signals emitted by BibValidationWorker to keep the GUI responsive."""
    progress = pyqtSignal(int, int)          # current, total
    entry_validated = pyqtSignal(int, dict)  # index, result dict
    log = pyqtSignal(str)
    finished = pyqtSignal(list)              # final list of result dicts
    error = pyqtSignal(str)


class BibValidationWorker(QRunnable):
    """Background worker that validates a list of parsed BibTeX entries against
    Crossref/OpenAlex without freezing the GUI. Rate-limited, retries transient
    failures, and caches lookups for the duration of the run."""

    def __init__(self, entries, rate_limit_delay=0.3, timeout=15):
        super().__init__()
        self.entries = entries
        self.rate_limit_delay = rate_limit_delay
        self.timeout = timeout
        self.signals = BibValidationSignals()
        self._stop_requested = False
        self._cache = {}

    def stop(self):
        self._stop_requested = True

    def _should_stop(self):
        return self._stop_requested

    def run(self):
        try:
            total = len(self.entries)
            self.signals.log.emit(f"🔎 Starting validation of {total} BibTeX entries...")

            results = []

            def on_progress(current, total_count, result):
                results.append(result)
                self.signals.entry_validated.emit(current - 1, result)
                self.signals.progress.emit(current, total_count)

            results = validate_bib_file(
                self.entries,
                progress_callback=on_progress,
                log_callback=self.signals.log.emit,
                should_stop=self._should_stop,
                cache=self._cache,
                rate_limit_delay=self.rate_limit_delay,
                timeout=self.timeout
            )

            if self._stop_requested:
                self.signals.log.emit("⏹️ Validation stopped by user.")
            else:
                self.signals.log.emit("✅ Validation complete.")

            self.signals.finished.emit(results)
        except Exception as e:
            self.signals.error.emit(f"Error during validation: {str(e)}")


class ThemeManager:
    DARK = {
        'bg': '#1e1e2e',
        'bg_secondary': '#313244',
        'bg_tertiary': '#45475a',
        'text': '#cdd6f4',
        'text_secondary': '#a6adc8',
        'accent': '#89b4fa',
        'accent_hover': '#74c7ec',
        'success': '#a6e3a1',
        'danger': '#f38ba8',
        'warning': '#f9e2af',
        'border': '#45475a',
        'input_bg': '#1a1b26',
        'progress_bg': '#313244',
        'progress_chunk': '#89b4fa',
        'scrollbar': '#45475a',
        'scrollbar_hover': '#585b70'
    }
    
    LIGHT = {
        'bg': '#f5f5f7',
        'bg_secondary': '#e8e8ed',
        'bg_tertiary': '#d4d4d9',
        'text': '#1a1b26',
        'text_secondary': '#5a5b6e',
        'accent': '#1e66f5',
        'accent_hover': '#1a5bdb',
        'success': '#2e7d32',
        'danger': '#c62828',
        'warning': '#f57c00',
        'border': '#c4c4c9',
        'input_bg': '#ffffff',
        'progress_bg': '#e8e8ed',
        'progress_chunk': '#1e66f5',
        'scrollbar': '#c4c4c9',
        'scrollbar_hover': '#a8a8b0'
    }
    
    @staticmethod
    def get_stylesheet(theme='light'):
        colors = ThemeManager.LIGHT if theme == 'light' else ThemeManager.DARK
        
        return f"""
            QMainWindow {{
                background-color: {colors['bg']};
            }}
            
            QLabel {{
                color: {colors['text']};
                font-size: 12px;
            }}
            
            QLineEdit, QTextEdit, QSpinBox, QComboBox {{
                background-color: {colors['input_bg']};
                color: {colors['text']};
                border: 1px solid {colors['border']};
                border-radius: 6px;
                padding: 8px;
                font-size: 12px;
            }}
            
            QLineEdit:focus, QTextEdit:focus {{
                border: 2px solid {colors['accent']};
            }}
            
            QPushButton {{
                background-color: {colors['bg_tertiary']};
                color: {colors['text']};
                border: none;
                border-radius: 6px;
                padding: 10px 20px;
                font-size: 12px;
                font-weight: bold;
            }}
            
            QPushButton:hover {{
                background-color: {colors['bg_secondary']};
            }}
            
            QPushButton:pressed {{
                background-color: {colors['bg_tertiary']};
            }}
            
            QPushButton#primary {{
                background-color: {colors['accent']};
                color: {colors['bg']};
            }}
            
            QPushButton#primary:hover {{
                background-color: {colors['accent_hover']};
            }}
            
            QPushButton#success {{
                background-color: {colors['success']};
                color: {colors['bg']};
            }}
            
            QPushButton#success:hover {{
                background-color: {colors['success']};
                opacity: 0.8;
            }}
            
            QPushButton#danger {{
                background-color: {colors['danger']};
                color: {colors['bg']};
            }}
            
            QPushButton#danger:hover {{
                background-color: {colors['danger']};
                opacity: 0.8;
            }}
            
            QGroupBox {{
                color: {colors['text']};
                border: 2px solid {colors['border']};
                border-radius: 8px;
                margin-top: 10px;
                padding-top: 10px;
                font-weight: bold;
            }}
            
            QGroupBox::title {{
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 5px 0 5px;
            }}
            
            QProgressBar {{
                border: 1px solid {colors['border']};
                border-radius: 6px;
                text-align: center;
                color: {colors['text']};
                background-color: {colors['progress_bg']};
            }}
            
            QProgressBar::chunk {{
                background-color: {colors['progress_chunk']};
                border-radius: 6px;
            }}
            
            QTabWidget::pane {{
                background-color: {colors['bg']};
                border: 1px solid {colors['border']};
                border-radius: 6px;
            }}
            
            QTabBar::tab {{
                background-color: {colors['bg_secondary']};
                color: {colors['text']};
                padding: 8px 16px;
                border-top-left-radius: 6px;
                border-top-right-radius: 6px;
            }}
            
            QTabBar::tab:selected {{
                background-color: {colors['bg_tertiary']};
            }}
            
            QTabBar::tab:hover {{
                background-color: {colors['border']};
            }}
            
            QScrollBar:vertical {{
                background-color: {colors['bg_secondary']};
                width: 12px;
                border-radius: 6px;
            }}
            
            QScrollBar::handle:vertical {{
                background-color: {colors['scrollbar']};
                border-radius: 6px;
                min-height: 20px;
            }}
            
            QScrollBar::handle:vertical:hover {{
                background-color: {colors['scrollbar_hover']};
            }}
            
            QStatusBar {{
                color: {colors['text']};
                background-color: {colors['bg_secondary']};
            }}
            
            QMenuBar {{
                background-color: {colors['bg_secondary']};
                color: {colors['text']};
            }}
            
            QMenuBar::item:selected {{
                background-color: {colors['bg_tertiary']};
            }}
            
            QMenu {{
                background-color: {colors['bg_secondary']};
                color: {colors['text']};
                border: 1px solid {colors['border']};
            }}
            
            QMenu::item:selected {{
                background-color: {colors['bg_tertiary']};
            }}
            
            QCheckBox {{
                color: {colors['text']};
                spacing: 8px;
            }}
            
            QCheckBox::indicator {{
                width: 18px;
                height: 18px;
                border: 2px solid {colors['border']};
                border-radius: 4px;
                background-color: {colors['input_bg']};
            }}
            
            QCheckBox::indicator:checked {{
                background-color: {colors['accent']};
                border-color: {colors['accent']};
            }}
            
            QCheckBox::indicator:hover {{
                border-color: {colors['accent']};
            }}
            
            QRadioButton {{
                color: {colors['text']};
            }}
            
            QComboBox::drop-down {{
                border: none;
            }}
            
            QComboBox QAbstractItemView {{
                background-color: {colors['input_bg']};
                color: {colors['text']};
                selection-background-color: {colors['accent']};
                selection-color: {colors['bg']};
            }}
        """

class OpenAlexGUI(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"📚 OpenAlex Paper Fetcher v{VERSION}")
        self.setMinimumSize(1000, 750)
        
        self.current_theme = 'light'
        self.output_dir = os.path.join(os.getcwd(), "refsfinder")
        self.generated_files = {}
        self.fetched_papers = []
        self.bib_validator_windows = []
        
        self.setup_ui()
        self.apply_theme()
        
        self.threadpool = QThreadPool()
        
        self.log_message("🚀 Welcome to OpenAlex Paper Fetcher v" + VERSION + "!")
        self.log_message("💡 Enter keywords and click 'Fetch Papers' to start")
        self.log_message("☀️ Light theme is active by default")
    
    def setup_ui(self):
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)
        main_layout.setSpacing(15)
        main_layout.setContentsMargins(20, 20, 20, 20)
        
        header_layout = QHBoxLayout()
        
        title = QLabel(f"📚 OpenAlex Paper Fetcher v{VERSION}")
        title.setStyleSheet("font-size: 24px; font-weight: bold;")
        header_layout.addWidget(title)
        header_layout.addStretch()
        
        self.theme_toggle = QPushButton("🌙 Dark")
        self.theme_toggle.setObjectName("primary")
        self.theme_toggle.setFixedWidth(100)
        self.theme_toggle.clicked.connect(self.toggle_theme)
        header_layout.addWidget(self.theme_toggle)
        
        main_layout.addLayout(header_layout)
        
        tabs = QTabWidget()
        main_layout.addWidget(tabs)
        
        search_tab = QWidget()
        tabs.addTab(search_tab, "🔍 Search")
        search_layout = QVBoxLayout(search_tab)
        
        search_group = QGroupBox("Search Parameters")
        search_group_layout = QGridLayout()
        search_group_layout.setSpacing(10)
        
        search_group_layout.addWidget(QLabel("Keywords:"), 0, 0)
        self.keywords_edit = QTextEdit()
        self.keywords_edit.setPlaceholderText("Enter keywords, one per line\nExample:\nHVAC\nYOLO\noccupancy detection")
        self.keywords_edit.setMaximumHeight(100)
        self.keywords_edit.setText("HVAC\nYOLO\noccupancy detection\nenergy efficiency")
        search_group_layout.addWidget(self.keywords_edit, 0, 1)
        
        search_group_layout.addWidget(QLabel("Max Results:"), 1, 0)
        self.max_results_spin = QSpinBox()
        self.max_results_spin.setRange(1, 500)
        self.max_results_spin.setValue(50)
        self.max_results_spin.setStyleSheet("padding: 5px;")
        search_group_layout.addWidget(self.max_results_spin, 1, 1)
        
        search_group_layout.addWidget(QLabel("Year Range:"), 2, 0)
        year_layout = QHBoxLayout()
        self.year_start_spin = QSpinBox()
        self.year_start_spin.setRange(1900, 2026)
        self.year_start_spin.setValue(2000)
        self.year_start_spin.setSpecialValueText("Any")
        year_layout.addWidget(self.year_start_spin)
        year_layout.addWidget(QLabel("to"))
        self.year_end_spin = QSpinBox()
        self.year_end_spin.setRange(1900, 2026)
        self.year_end_spin.setValue(2025)
        self.year_end_spin.setSpecialValueText("Any")
        year_layout.addWidget(self.year_end_spin)
        year_layout.addStretch()
        search_group_layout.addLayout(year_layout, 2, 1)
        
        search_group_layout.addWidget(QLabel("Filters:"), 3, 0)
        filter_layout = QHBoxLayout()
        self.abstract_filter_checkbox = QCheckBox("Only include papers with abstracts")
        self.abstract_filter_checkbox.setChecked(True)
        self.abstract_filter_checkbox.setToolTip("Filter out papers that don't have an abstract")
        filter_layout.addWidget(self.abstract_filter_checkbox)
        self.citation_filter_checkbox = QCheckBox("Only include papers with citations (sort high → low)")
        self.citation_filter_checkbox.setChecked(False)
        self.citation_filter_checkbox.setToolTip("Filter out papers with zero citations and sort the rest by citation count, highest first")
        filter_layout.addWidget(self.citation_filter_checkbox)
        self.journal_filter_checkbox = QCheckBox("Only include journal papers (no thesis/conference)")
        self.journal_filter_checkbox.setChecked(False)
        self.journal_filter_checkbox.setToolTip("Keep only journal articles; excludes theses, conference papers, preprints, books, and other non-journal types")
        filter_layout.addWidget(self.journal_filter_checkbox)
        filter_layout.addStretch()
        search_group_layout.addLayout(filter_layout, 3, 1)
        
        search_group_layout.addWidget(QLabel("Output Folder:"), 4, 0)
        output_layout = QHBoxLayout()
        self.output_dir_edit = QLineEdit()
        self.output_dir_edit.setText(self.output_dir)
        self.output_dir_edit.setReadOnly(True)
        output_layout.addWidget(self.output_dir_edit)
        
        self.browse_btn = QPushButton("📁 Browse")
        self.browse_btn.setObjectName("primary")
        self.browse_btn.clicked.connect(self.browse_output_dir)
        output_layout.addWidget(self.browse_btn)
        
        self.open_folder_btn = QPushButton("📂 Open Folder")
        self.open_folder_btn.setObjectName("success")
        self.open_folder_btn.clicked.connect(self.open_output_folder)
        output_layout.addWidget(self.open_folder_btn)
        
        search_group_layout.addLayout(output_layout, 4, 1)
        
        search_group.setLayout(search_group_layout)
        search_layout.addWidget(search_group)
        
        action_layout = QHBoxLayout()
        action_layout.addStretch()
        
        self.fetch_btn = QPushButton("🚀 Fetch Papers")
        self.fetch_btn.setObjectName("primary")
        self.fetch_btn.setMinimumWidth(150)
        self.fetch_btn.clicked.connect(self.start_fetch)
        action_layout.addWidget(self.fetch_btn)
        
        self.explore_btn = QPushButton("📚 Explore Papers")
        self.explore_btn.setObjectName("success")
        self.explore_btn.setMinimumWidth(150)
        self.explore_btn.setEnabled(True)
        self.explore_btn.clicked.connect(self.open_explorer)
        action_layout.addWidget(self.explore_btn)
        
        self.check_bib_btn = QPushButton("🔎 Check BibTeX References")
        self.check_bib_btn.setObjectName("primary")
        self.check_bib_btn.setMinimumWidth(220)
        self.check_bib_btn.setToolTip("Select a .bib file and verify each reference against Crossref/OpenAlex")
        self.check_bib_btn.clicked.connect(self.open_bib_validator)
        action_layout.addWidget(self.check_bib_btn)
        
        self.clear_btn = QPushButton("🗑️ Clear Log")
        self.clear_btn.clicked.connect(self.clear_log)
        action_layout.addWidget(self.clear_btn)
        
        search_layout.addLayout(action_layout)
        
        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        search_layout.addWidget(self.progress_bar)
        
        log_group = QGroupBox("Terminal Log")
        log_layout = QVBoxLayout()
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setFont(QFont("Courier New", 10))
        log_layout.addWidget(self.log_text)
        log_group.setLayout(log_layout)
        search_layout.addWidget(log_group)
        
        about_tab = QWidget()
        tabs.addTab(about_tab, "ℹ️ About")
        about_layout = QVBoxLayout(about_tab)
        
        about_text = QTextEdit()
        about_text.setReadOnly(True)
        about_text.setStyleSheet("background-color: transparent; border: none; font-size: 14px;")
        about_text.setHtml(f"""
            <h2>OpenAlex Paper Fetcher</h2>
            <p><b>Version:</b> {VERSION}</p>
            <p><b>Description:</b></p>
            <p>This tool fetches academic papers from OpenAlex and generates multiple output formats for literature review and citation management.</p>
            
            <h3 style="margin-top: 20px;">Generated Files:</h3>
            <ul>
                <li><b>refs.bib</b> - Clean bibliography for Overleaf</li>
                <li><b>refs_abstract.bib</b> - With full abstracts for review</li>
                <li><b>refs_full.bib</b> - Full metadata with abstracts</li>
                <li><b>refs_with_summary.bib</b> - With AI summaries for Aider</li>
                <li><b>papers.json</b> - Complete OpenAlex metadata</li>
                <li><b>papers.xlsx</b> - Sortable/filterable spreadsheet</li>
                <li><b>abstracts.md</b> - Readable literature review</li>
            </ul>
            
            <h3 style="margin-top: 20px;">Features:</h3>
            <ul>
                <li>✓ Modern PyQt6 interface</li>
                <li>✓ Dark/Light theme toggle</li>
                <li>✓ Keyword search with multi-line input</li>
                <li>✓ Year range filtering</li>
                <li>✓ Abstract filter - only include papers with abstracts</li>
                <li>✓ Citation filter - only include papers with citations, sorted high to low</li>
                <li>✓ Journal filter - only include journal papers, excluding theses and conference papers</li>
                <li>✓ Real-time progress tracking</li>
                <li>✓ Terminal-like log output</li>
                <li>✓ Custom output folder selection</li>
                <li>✓ Open folder with one click</li>
                <li>✓ Paper Explorer with modern card layout</li>
                <li>✓ Click DOI to open paper in browser</li>
                <li>✓ Remove papers from collection</li>
                <li>✓ Save changes after removal</li>
                <li>✓ BibTeX Reference Checker - verify existing .bib files against Crossref/OpenAlex</li>
                <li>✓ Conservative validation status (VALID, warnings, invalid, duplicate, unverified)</li>
                <li>✓ Automatic metadata correction for reliably matched references</li>
                <li>✓ Remove invalid/duplicate references with confirmation, export cleaned .bib</li>
                <li>✓ Validation report export (CSV/Excel) for pre-publication checks</li>
            </ul>
            
            <h3 style="margin-top: 20px;">Tips:</h3>
            <ul>
                <li>Enter one keyword per line for better results</li>
                <li>Use year filters to narrow down results</li>
                <li>Enable "Only include papers with abstracts" for cleaner results</li>
                <li>Click "Explore Papers" to browse results in a modern card view</li>
                <li>Click "Remove" to delete papers from your collection</li>
                <li>Click "Save Changes" to regenerate files without removed papers</li>
                <li>Toggle between Dark and Light themes using the button in the header</li>
                <li>Click "Check BibTeX References" to validate an existing .bib file against Crossref/OpenAlex</li>
                <li>In the Reference Checker, use "Fix References" to auto-correct reliable matches, then "Save Validated BibTeX"</li>
                <li>The original .bib file is never modified - results are always saved to a new file</li>
            </ul>
        """)
        about_layout.addWidget(about_text)
        
        self.status_bar = self.statusBar()
        self.status_bar.showMessage("Ready")
    
    def apply_theme(self):
        stylesheet = ThemeManager.get_stylesheet(self.current_theme)
        self.setStyleSheet(stylesheet)
        
        if self.current_theme == 'light':
            self.theme_toggle.setText("🌙 Dark")
        else:
            self.theme_toggle.setText("☀️ Light")
    
    def toggle_theme(self):
        if self.current_theme == 'light':
            self.current_theme = 'dark'
        else:
            self.current_theme = 'light'
        self.apply_theme()
        self.log_message(f"🎨 Switched to {self.current_theme.capitalize()} theme")
    
    def browse_output_dir(self):
        dir_path = QFileDialog.getExistingDirectory(
            self, 
            "Select Output Directory",
            self.output_dir_edit.text()
        )
        if dir_path:
            self.output_dir = dir_path
            self.output_dir_edit.setText(dir_path)
            self.log_message(f"📁 Output directory set to: {dir_path}")
    
    def open_output_folder(self):
        if os.path.exists(self.output_dir):
            if sys.platform == 'win32':
                os.startfile(self.output_dir)
            elif sys.platform == 'darwin':
                os.system(f'open "{self.output_dir}"')
            else:
                os.system(f'xdg-open "{self.output_dir}"')
        else:
            QMessageBox.warning(self, "Folder Not Found", 
                               f"The folder does not exist yet.\nPlease fetch papers first.")
    
    def log_message(self, message):
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.log_text.append(f"[{timestamp}] {message}")
        cursor = self.log_text.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        self.log_text.setTextCursor(cursor)
    
    def clear_log(self):
        self.log_text.clear()
        self.log_message("🗑️ Log cleared")
    
    def update_progress(self, current, total):
        if current < 0:
            self.progress_bar.setVisible(False)
            return
        
        self.progress_bar.setVisible(True)
        self.progress_bar.setMaximum(total)
        self.progress_bar.setValue(min(current, total))
        
        if current >= total:
            self.progress_bar.setVisible(False)
    
    def start_fetch(self):
        keywords_text = self.keywords_edit.toPlainText().strip()
        if not keywords_text:
            QMessageBox.warning(self, "Missing Keywords", 
                              "Please enter at least one keyword.")
            return
        
        keywords = [k.strip() for k in keywords_text.split('\n') if k.strip()]
        
        max_results = self.max_results_spin.value()
        year_start = self.year_start_spin.value() if self.year_start_spin.value() > 1900 else None
        year_end = self.year_end_spin.value() if self.year_end_spin.value() < 2026 else None
        only_with_abstract = self.abstract_filter_checkbox.isChecked()
        only_with_citations = self.citation_filter_checkbox.isChecked()
        only_journal_papers = self.journal_filter_checkbox.isChecked()

        self.fetch_btn.setEnabled(False)
        self.fetch_btn.setText("⏳ Fetching...")
        self.explore_btn.setEnabled(False)
        self.status_bar.showMessage("Fetching papers...")
        
        self.log_message("")
        self.log_message("🚀 Starting paper fetch...")
        self.log_message("=" * 60)
        
        worker = FetchWorker(keywords, max_results, year_start, year_end, self.output_dir, only_with_abstract, only_with_citations, only_journal_papers)
        worker.signals.progress.connect(self.update_progress)
        worker.signals.log.connect(self.log_message)
        worker.signals.error.connect(self.on_error)
        worker.signals.finished.connect(self.on_finished)
        
        self.threadpool.start(worker)
    
    def on_error(self, error_msg):
        self.log_message(f"\n❌ Error: {error_msg}")
        self.status_bar.showMessage(f"Error: {error_msg}")
        self.fetch_btn.setEnabled(True)
        self.fetch_btn.setText("🚀 Fetch Papers")
        self.progress_bar.setVisible(False)
        
        QMessageBox.critical(self, "Error", error_msg)
    
    def on_finished(self, files):
        self.generated_files = files
        
        if 'papers' in files and files['papers']:
            self.fetched_papers = files['papers']
            self.explore_btn.setEnabled(True)
            self.log_message(f"📚 {len(self.fetched_papers)} papers available for exploration")
        
        self.log_message("\n" + "=" * 60)
        self.log_message("✅ COMPLETE! All files saved to:")
        self.log_message(f"   📁 {self.output_dir}/")
        self.log_message("-" * 60)
        
        if 'original_count' in files and 'filtered_count' in files:
            self.log_message(f"📊 Statistics:")
            self.log_message(f"   • Original papers: {files['original_count']}")
            self.log_message(f"   • Papers with abstracts: {files['filtered_count']}")
            if files['original_count'] > files['filtered_count']:
                self.log_message(f"   • Removed: {files['original_count'] - files['filtered_count']} papers without abstracts")
            self.log_message("-" * 60)
        
        self.log_message("📄 Generated Files:")
        for key, path in files.items():
            if key not in ['original_count', 'filtered_count', 'papers']:
                self.log_message(f"   • {os.path.basename(path)}")
        
        self.status_bar.showMessage(f"✅ Complete! Generated {len(files)-3} files")
        self.fetch_btn.setEnabled(True)
        self.fetch_btn.setText("🚀 Fetch Papers")
        self.progress_bar.setVisible(False)
        
        if self.fetched_papers:
            reply = QMessageBox.question(
                self, 
                "Success", 
                f"✅ Successfully generated {len(files)-3} files!\n\nDo you want to explore the papers in the Paper Explorer?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
            )
            if reply == QMessageBox.StandardButton.Yes:
                self.open_explorer()
    
    def open_explorer(self):
        """Open the Paper Explorer window"""
        # If no papers in memory, try to load from JSON
        if not self.fetched_papers:
            json_file = os.path.join(self.output_dir, "papers.json")
            if os.path.exists(json_file):
                try:
                    with open(json_file, 'r', encoding='utf8') as f:
                        self.fetched_papers = json.load(f)
                        self.explore_btn.setEnabled(True)
                        self.log_message(f"📚 Loaded {len(self.fetched_papers)} papers from JSON")
                except Exception as e:
                    self.log_message(f"⚠️ Could not load papers from JSON: {e}")
        
        # If still no papers, show message with helpful info
        if not self.fetched_papers:
            QMessageBox.information(
                self, 
                "No Papers Found", 
                "No papers to explore.\n\n"
                "Please follow these steps:\n"
                "1. Click 'Fetch Papers' to search for papers\n"
                "2. Wait for the search to complete\n"
                "3. Then click 'Explore Papers' again\n\n"
                f"Output folder: {self.output_dir}"
            )
            return
        
        # Open explorer with papers
        explorer = PaperExplorerWindow(self.fetched_papers, self)
        explorer.show()
    
    def open_bib_validator(self):
        """Prompt the user to select a .bib file and open the BibTeX Reference
        Checker window. This is fully independent of the OpenAlex fetch workflow
        and never modifies the selected file."""
        path, _ = QFileDialog.getOpenFileName(
            self, "Select BibTeX File to Check", self.output_dir, "BibTeX Files (*.bib);;All Files (*)"
        )
        if not path:
            return
        
        try:
            entries, parse_errors = parse_bib_file(path)
        except Exception as e:
            QMessageBox.critical(self, "Error Reading File", f"Could not read/parse the BibTeX file:\n{e}")
            return
        
        if parse_errors:
            self.log_message(f"⚠️ {len(parse_errors)} issue(s) while parsing {os.path.basename(path)}:")
            for err in parse_errors[:10]:
                self.log_message(f"   • {err}")
        
        if not entries:
            QMessageBox.warning(
                self, "No Entries Found",
                f"No valid BibTeX entries could be parsed from:\n{path}\n\n"
                + ("\n".join(parse_errors[:5]) if parse_errors else "The file may be empty or malformed.")
            )
            return
        
        self.log_message(f"🔎 Loaded {len(entries)} BibTeX entries from {os.path.basename(path)}")
        
        validator = BibValidatorWindow(path, entries, self)
        self.bib_validator_windows.append(validator)
        validator.show()

def main():
    app = QApplication(sys.argv)
    app.setStyle('Fusion')
    app.setWindowIcon(QIcon())
    
    window = OpenAlexGUI()
    window.show()
    
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
