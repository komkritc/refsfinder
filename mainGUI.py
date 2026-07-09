#!/usr/bin/env python3

"""
OpenAlex Paper Fetcher - Modern GUI with PyQt6
Features: Keyword search, year filter, progress tracking, file management, Dark/Light theme
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
from queue import Queue
from PyQt6.QtWidgets import *
from PyQt6.QtCore import *
from PyQt6.QtGui import *

# ==========================================================
# Backend Functions (from original script)
# ==========================================================

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

def save_all_outputs(papers, output_dir, query, only_with_abstract=False):
    """Generate all output files"""
    # Create output directory
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    
    # Filter papers if requested
    if only_with_abstract:
        original_count = len(papers)
        papers = filter_papers_with_abstract(papers)
        filtered_count = len(papers)
        print(f"Filtered: {original_count} -> {filtered_count} papers with abstracts")
    
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
    files['original_count'] = len(papers)  # This will be updated if filtering was applied
    
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
# PyQt6 GUI Application with Theme Support
# ==========================================================

class WorkerSignals(QObject):
    """Defines signals available from running worker thread"""
    progress = pyqtSignal(int, int)  # current, total
    log = pyqtSignal(str)  # log message
    finished = pyqtSignal(dict)  # results
    error = pyqtSignal(str)  # error message

class FetchWorker(QRunnable):
    """Worker thread for fetching papers"""
    def __init__(self, keywords, max_results, year_start, year_end, output_dir, only_with_abstract):
        super().__init__()
        self.keywords = keywords
        self.max_results = max_results
        self.year_start = year_start
        self.year_end = year_end
        self.output_dir = output_dir
        self.only_with_abstract = only_with_abstract
        self.signals = WorkerSignals()
        self.is_running = True
    
    def run(self):
        try:
            # Build query
            query = " ".join(self.keywords)
            self.signals.log.emit(f"🔍 Searching: {query}")
            
            if self.year_start or self.year_end:
                year_range = f"{self.year_start or 'any'} - {self.year_end or 'any'}"
                self.signals.log.emit(f"📅 Year range: {year_range}")
            
            self.signals.log.emit(f"📊 Max results: {self.max_results}")
            if self.only_with_abstract:
                self.signals.log.emit(f"📝 Filter: Only papers with abstracts")
            self.signals.log.emit("-" * 50)
            
            # Fetch papers with progress
            def progress_callback(current, total, error=None):
                if error:
                    self.signals.error.emit(error)
                else:
                    self.signals.progress.emit(current, total)
                    if current % 5 == 0:  # Log every 5 papers
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
            
            # Apply abstract filter if requested
            if self.only_with_abstract:
                papers = filter_papers_with_abstract(papers)
                filtered_count = len(papers)
                self.signals.log.emit(f"📝 Filtered to {filtered_count} papers with abstracts ({original_count - filtered_count} removed)")
            
            self.signals.log.emit("📝 Generating output files...")
            
            # Generate outputs
            files = save_all_outputs(papers, self.output_dir, query, self.only_with_abstract)
            
            # Add filter info to files
            files['original_count'] = original_count
            files['filtered_count'] = len(papers)
            
            self.signals.log.emit("\n📄 Generated files:")
            for key, path in files.items():
                if key not in ['original_count', 'filtered_count']:
                    self.signals.log.emit(f"   • {os.path.basename(path)}")
            
            self.signals.finished.emit(files)
            
        except Exception as e:
            self.signals.error.emit(f"Error: {str(e)}")

class ThemeManager:
    """Theme management for the application"""
    
    # Dark Theme Colors
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
    
    # Light Theme Colors
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
    def get_stylesheet(theme='dark'):
        """Get the complete stylesheet for the selected theme"""
        colors = ThemeManager.DARK if theme == 'dark' else ThemeManager.LIGHT
        
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
        self.setWindowTitle("📚 OpenAlex Paper Fetcher")
        self.setMinimumSize(1000, 750)
        
        # Initialize theme
        self.current_theme = 'dark'  # 'dark' or 'light'
        
        # Initialize variables
        self.output_dir = os.path.join(os.getcwd(), "refsfinder")
        self.generated_files = {}
        
        # Setup UI
        self.setup_ui()
        
        # Apply theme
        self.apply_theme()
        
        # Thread pool
        self.threadpool = QThreadPool()
        
        # Log initial message
        self.log_message("🚀 Welcome to OpenAlex Paper Fetcher!")
        self.log_message("💡 Enter keywords and click 'Fetch Papers' to start")
    
    def setup_ui(self):
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)
        main_layout.setSpacing(15)
        main_layout.setContentsMargins(20, 20, 20, 20)
        
        # Header with theme toggle
        header_layout = QHBoxLayout()
        
        title = QLabel("📚 OpenAlex Paper Fetcher")
        title.setStyleSheet("font-size: 24px; font-weight: bold;")
        header_layout.addWidget(title)
        header_layout.addStretch()
        
        # Theme toggle
        self.theme_toggle = QPushButton("🌙 Dark")
        self.theme_toggle.setObjectName("primary")
        self.theme_toggle.setFixedWidth(100)
        self.theme_toggle.clicked.connect(self.toggle_theme)
        header_layout.addWidget(self.theme_toggle)
        
        version = QLabel("v2.2")
        version.setStyleSheet("color: #6c7086; font-size: 12px;")
        header_layout.addWidget(version)
        main_layout.addLayout(header_layout)
        
        # Main content - Tab widget
        tabs = QTabWidget()
        main_layout.addWidget(tabs)
        
        # Tab 1: Search
        search_tab = QWidget()
        tabs.addTab(search_tab, "🔍 Search")
        search_layout = QVBoxLayout(search_tab)
        
        # Search group
        search_group = QGroupBox("Search Parameters")
        search_group_layout = QGridLayout()
        search_group_layout.setSpacing(10)
        
        # Keywords
        search_group_layout.addWidget(QLabel("Keywords:"), 0, 0)
        self.keywords_edit = QTextEdit()
        self.keywords_edit.setPlaceholderText("Enter keywords, one per line\nExample:\nHVAC\nYOLO\noccupancy detection")
        self.keywords_edit.setMaximumHeight(100)
        self.keywords_edit.setText("HVAC\nYOLO\noccupancy detection\nenergy efficiency")
        search_group_layout.addWidget(self.keywords_edit, 0, 1)
        
        # Max results
        search_group_layout.addWidget(QLabel("Max Results:"), 1, 0)
        self.max_results_spin = QSpinBox()
        self.max_results_spin.setRange(1, 500)
        self.max_results_spin.setValue(50)
        self.max_results_spin.setStyleSheet("padding: 5px;")
        search_group_layout.addWidget(self.max_results_spin, 1, 1)
        
        # Year range
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
        
        # Filter options
        search_group_layout.addWidget(QLabel("Filters:"), 3, 0)
        filter_layout = QHBoxLayout()
        
        self.abstract_filter_checkbox = QCheckBox("Only include papers with abstracts")
        self.abstract_filter_checkbox.setChecked(True) 
        self.abstract_filter_checkbox.setToolTip("Filter out papers that don't have an abstract")
        filter_layout.addWidget(self.abstract_filter_checkbox)
        
        filter_layout.addStretch()
        search_group_layout.addLayout(filter_layout, 3, 1)
        
        # Output directory
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
        
        # Action buttons
        action_layout = QHBoxLayout()
        action_layout.addStretch()
        
        self.fetch_btn = QPushButton("🚀 Fetch Papers")
        self.fetch_btn.setObjectName("primary")
        self.fetch_btn.setMinimumWidth(150)
        self.fetch_btn.clicked.connect(self.start_fetch)
        action_layout.addWidget(self.fetch_btn)
        
        self.clear_btn = QPushButton("🗑️ Clear Log")
        self.clear_btn.clicked.connect(self.clear_log)
        action_layout.addWidget(self.clear_btn)
        
        search_layout.addLayout(action_layout)
        
        # Progress bar
        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        search_layout.addWidget(self.progress_bar)
        
        # Log output
        log_group = QGroupBox("Terminal Log")
        log_layout = QVBoxLayout()
        
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setFont(QFont("Courier New", 10))
        log_layout.addWidget(self.log_text)
        log_group.setLayout(log_layout)
        search_layout.addWidget(log_group)
        
        # Tab 2: About
        about_tab = QWidget()
        tabs.addTab(about_tab, "ℹ️ About")
        about_layout = QVBoxLayout(about_tab)
        
        about_text = QTextEdit()
        about_text.setReadOnly(True)
        about_text.setStyleSheet("background-color: transparent; border: none; font-size: 14px;")
        about_text.setHtml("""
            <h2>OpenAlex Paper Fetcher</h2>
            <p><b>Version:</b> 2.2</p>
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
                <li>✓ Real-time progress tracking</li>
                <li>✓ Terminal-like log output</li>
                <li>✓ Custom output folder selection</li>
                <li>✓ Open folder with one click</li>
            </ul>
            
            <h3 style="margin-top: 20px;">Tips:</h3>
            <ul>
                <li>Enter one keyword per line for better results</li>
                <li>Use year filters to narrow down results</li>
                <li>Enable "Only include papers with abstracts" for cleaner results</li>
                <li>Results are cached for 24 hours</li>
                <li>Generated files include AI summaries for LLM-assisted writing</li>
                <li>Toggle between Dark and Light themes using the button in the header</li>
            </ul>
        """)
        about_layout.addWidget(about_text)
        
        # Status bar
        self.status_bar = self.statusBar()
        self.status_bar.showMessage("Ready")
    
    def apply_theme(self):
        """Apply the current theme"""
        stylesheet = ThemeManager.get_stylesheet(self.current_theme)
        self.setStyleSheet(stylesheet)
        
        # Update theme toggle button text
        if self.current_theme == 'dark':
            self.theme_toggle.setText("☀️ Light")
        else:
            self.theme_toggle.setText("🌙 Dark")
    
    def toggle_theme(self):
        """Toggle between dark and light themes"""
        if self.current_theme == 'dark':
            self.current_theme = 'light'
        else:
            self.current_theme = 'dark'
        self.apply_theme()
        self.log_message(f"🎨 Switched to {self.current_theme.capitalize()} theme")
    
    def browse_output_dir(self):
        """Browse for output directory"""
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
        """Open the output folder in file manager"""
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
        """Add message to log"""
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.log_text.append(f"[{timestamp}] {message}")
        # Auto-scroll to bottom
        cursor = self.log_text.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        self.log_text.setTextCursor(cursor)
    
    def clear_log(self):
        """Clear the log"""
        self.log_text.clear()
        self.log_message("🗑️ Log cleared")
    
    def update_progress(self, current, total):
        """Update progress bar"""
        if current < 0:
            self.progress_bar.setVisible(False)
            return
        
        self.progress_bar.setVisible(True)
        self.progress_bar.setMaximum(total)
        self.progress_bar.setValue(min(current, total))
        
        if current >= total:
            self.progress_bar.setVisible(False)
    
    def start_fetch(self):
        """Start the fetch process in a worker thread"""
        # Get keywords
        keywords_text = self.keywords_edit.toPlainText().strip()
        if not keywords_text:
            QMessageBox.warning(self, "Missing Keywords", 
                              "Please enter at least one keyword.")
            return
        
        keywords = [k.strip() for k in keywords_text.split('\n') if k.strip()]
        
        # Get parameters
        max_results = self.max_results_spin.value()
        year_start = self.year_start_spin.value() if self.year_start_spin.value() > 1900 else None
        year_end = self.year_end_spin.value() if self.year_end_spin.value() < 2026 else None
        only_with_abstract = self.abstract_filter_checkbox.isChecked()
        
        # Disable fetch button
        self.fetch_btn.setEnabled(False)
        self.fetch_btn.setText("⏳ Fetching...")
        self.status_bar.showMessage("Fetching papers...")
        
        # Clear log
        self.log_message("")
        self.log_message("🚀 Starting paper fetch...")
        self.log_message("=" * 60)
        
        # Create worker
        worker = FetchWorker(keywords, max_results, year_start, year_end, self.output_dir, only_with_abstract)
        worker.signals.progress.connect(self.update_progress)
        worker.signals.log.connect(self.log_message)
        worker.signals.error.connect(self.on_error)
        worker.signals.finished.connect(self.on_finished)
        
        # Start worker
        self.threadpool.start(worker)
    
    def on_error(self, error_msg):
        """Handle error from worker"""
        self.log_message(f"\n❌ Error: {error_msg}")
        self.status_bar.showMessage(f"Error: {error_msg}")
        self.fetch_btn.setEnabled(True)
        self.fetch_btn.setText("🚀 Fetch Papers")
        self.progress_bar.setVisible(False)
        
        QMessageBox.critical(self, "Error", error_msg)
    
    def on_finished(self, files):
        """Handle completion of fetch"""
        self.generated_files = files
        
        self.log_message("\n" + "=" * 60)
        self.log_message("✅ COMPLETE! All files saved to:")
        self.log_message(f"   📁 {self.output_dir}/")
        self.log_message("-" * 60)
        
        # Show filter stats if available
        if 'original_count' in files and 'filtered_count' in files:
            self.log_message(f"📊 Statistics:")
            self.log_message(f"   • Original papers: {files['original_count']}")
            self.log_message(f"   • Papers with abstracts: {files['filtered_count']}")
            if files['original_count'] > files['filtered_count']:
                self.log_message(f"   • Removed: {files['original_count'] - files['filtered_count']} papers without abstracts")
            self.log_message("-" * 60)
        
        self.log_message("📄 Generated Files:")
        for key, path in files.items():
            if key not in ['original_count', 'filtered_count']:
                self.log_message(f"   • {os.path.basename(path)}")
        
        self.status_bar.showMessage(f"✅ Complete! Generated {len(files)-2} files")
        self.fetch_btn.setEnabled(True)
        self.fetch_btn.setText("🚀 Fetch Papers")
        self.progress_bar.setVisible(False)
        
        # Ask if user wants to open folder
        reply = QMessageBox.question(
            self, 
            "Success", 
            f"✅ Successfully generated {len(files)-2} files!\n\nDo you want to open the output folder?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if reply == QMessageBox.StandardButton.Yes:
            self.open_output_folder()

def main():
    app = QApplication(sys.argv)
    app.setStyle('Fusion')
    
    # Set application icon
    app.setWindowIcon(QIcon())
    
    window = OpenAlexGUI()
    window.show()
    
    sys.exit(app.exec())

if __name__ == "__main__":
    main()