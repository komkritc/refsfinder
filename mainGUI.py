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
from queue import Queue
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

def save_all_outputs(papers, output_dir, query, only_with_abstract=False, only_with_citations=False):
    """Generate all output files"""
    # Create output directory
    Path(output_dir).mkdir(parents=True, exist_ok=True)

    # Filter papers if requested
    if only_with_abstract:
        original_count = len(papers)
        papers = filter_papers_with_abstract(papers)
        filtered_count = len(papers)
        print(f"Filtered: {original_count} -> {filtered_count} papers with abstracts")

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
                    self.parent().citation_filter_checkbox.isChecked() if self.parent() else False
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
# Main GUI Application
# ==========================================================

class WorkerSignals(QObject):
    progress = pyqtSignal(int, int)
    log = pyqtSignal(str)
    finished = pyqtSignal(dict)
    error = pyqtSignal(str)

class FetchWorker(QRunnable):
    def __init__(self, keywords, max_results, year_start, year_end, output_dir, only_with_abstract, only_with_citations=False):
        super().__init__()
        self.keywords = keywords
        self.max_results = max_results
        self.year_start = year_start
        self.year_end = year_end
        self.output_dir = output_dir
        self.only_with_abstract = only_with_abstract
        self.only_with_citations = only_with_citations
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

            if self.only_with_citations:
                before_citation_filter = len(papers)
                papers = filter_and_sort_by_citations(papers)
                self.signals.log.emit(f"⭐ Filtered to {len(papers)} papers with citations, sorted high to low ({before_citation_filter - len(papers)} removed)")

            self.signals.log.emit("📝 Generating output files...")

            files = save_all_outputs(papers, self.output_dir, query, self.only_with_abstract, self.only_with_citations)
            
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
                <li>✓ Real-time progress tracking</li>
                <li>✓ Terminal-like log output</li>
                <li>✓ Custom output folder selection</li>
                <li>✓ Open folder with one click</li>
                <li>✓ Paper Explorer with modern card layout</li>
                <li>✓ Click DOI to open paper in browser</li>
                <li>✓ Remove papers from collection</li>
                <li>✓ Save changes after removal</li>
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

        self.fetch_btn.setEnabled(False)
        self.fetch_btn.setText("⏳ Fetching...")
        self.explore_btn.setEnabled(False)
        self.status_bar.showMessage("Fetching papers...")
        
        self.log_message("")
        self.log_message("🚀 Starting paper fetch...")
        self.log_message("=" * 60)
        
        worker = FetchWorker(keywords, max_results, year_start, year_end, self.output_dir, only_with_abstract, only_with_citations)
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

def main():
    app = QApplication(sys.argv)
    app.setStyle('Fusion')
    app.setWindowIcon(QIcon())
    
    window = OpenAlexGUI()
    window.show()
    
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
