#!/usr/bin/env python3

"""
OpenAlex Paper Fetcher - Enhanced for AI-Assisted Writing Workflow
Generates multiple output formats for literature review and citation management
"""

import requests
import json
import time
import argparse
from datetime import datetime, timedelta
from tqdm import tqdm
import re
import pandas as pd
from pathlib import Path
import os

# ==========================================================
# CONFIG
# ==========================================================

KEYWORDS = [
    "HVAC",
    "YOLO",
    "occupancy detection",
    "energy efficiency"
]

MAX_RESULTS = 50
OUTPUT_DIR = "refsfinder"
CACHE_FILE = "openalex_cache.json"
CACHE_MAX_AGE_HOURS = 24

# ==========================================================
# Utility Functions
# ==========================================================

def safe_get(data, *keys, default=""):
    """Safely get nested dictionary values"""
    for key in keys:
        if data is None:
            return default
        if isinstance(data, dict):
            data = data.get(key)
        else:
            return default
    return data if data is not None else default

def fetch_with_retry(url, params=None, headers=None, max_retries=3):
    """Fetch URL with exponential backoff retry logic"""
    for attempt in range(max_retries):
        try:
            r = requests.get(url, params=params, headers=headers, timeout=30)
            r.raise_for_status()
            return r
        except requests.exceptions.RequestException as e:
            if attempt == max_retries - 1:
                raise
            wait_time = 2 ** attempt
            print(f"Retry {attempt + 1}/{max_retries} after {wait_time}s...")
            time.sleep(wait_time)
    return None

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

def generate_ai_summary(title, abstract, keywords):
    """Generate a concise AI-friendly summary for citation decisions"""
    # Extract key elements from abstract
    if not abstract:
        return f"Paper on {title[:60]}..."
    
    # Take first 150 chars that contain the main contribution
    sentences = abstract.split('. ')
    key_sentence = sentences[0] if sentences else abstract
    
    # Truncate and add context
    summary = key_sentence[:200]
    if len(key_sentence) > 200:
        summary = summary + "..."
    
    # Add keyword context
    if keywords:
        summary += f" Keywords: {', '.join(keywords[:3])}."
    
    return summary

def extract_keywords_from_paper(paper):
    """Extract keywords from OpenAlex data"""
    keywords = []
    
    # Get from concepts
    concepts = paper.get("concepts", [])
    if concepts:
        for concept in concepts[:5]:
            if concept and isinstance(concept, dict):
                name = concept.get("display_name")
                if name:
                    keywords.append(name)
    
    # Get from keywords if available
    paper_keywords = paper.get("keywords", [])
    if paper_keywords:
        for kw in paper_keywords:
            if kw and isinstance(kw, dict):
                name = kw.get("display_name")
                if name:
                    keywords.append(name)
    
    # Remove duplicates and limit
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
    # Get author surname
    authorships = paper.get("authorships", [])
    if authorships and isinstance(authorships, list):
        first_author = authorships[0].get("author", {}).get("display_name", "") if authorships else ""
        surname = first_author.split()[-1] if first_author else "unknown"
    else:
        surname = "unknown"
    
    # Get year
    year = paper.get("publication_year", datetime.now().year)
    
    # Get first significant title word
    title = paper.get("title", "")
    title_words = title.split() if title else []
    # Skip common words
    skip_words = {'a', 'an', 'the', 'on', 'of', 'for', 'with', 'using', 'toward', 'towards', 'and', 'in', 'to'}
    first_word = next((word for word in title_words if word.lower() not in skip_words), "paper")
    # Remove special characters
    first_word = re.sub(r'[^a-zA-Z0-9]', '', first_word)
    
    # Generate clean key
    key = f"{surname}{year}{first_word[:5]}"
    key = re.sub(r'[^a-zA-Z0-9]', '', key)
    return key

def paper_to_bibtex(paper, include_full=False, include_ai_summary=False, include_abstract=False):
    """Convert paper to BibTeX entry with robust error handling"""
    # Get basic metadata with safe access
    title = paper.get("title", "") or ""
    year = paper.get("publication_year", "") or ""
    doi = paper.get("doi", "") or ""
    
    # Get authors safely
    authorships = paper.get("authorships", [])
    authors = []
    if authorships and isinstance(authorships, list):
        for auth in authorships:
            if auth and isinstance(auth, dict):
                author_name = auth.get("author", {}).get("display_name", "") if auth.get("author") else ""
                if author_name:
                    # Convert to "Last, F. M." format
                    parts = author_name.split()
                    if len(parts) > 1:
                        last = parts[-1]
                        initials = " ".join([f"{p[0]}." for p in parts[:-1]])
                        authors.append(f"{last}, {initials}")
                    else:
                        authors.append(author_name)
    
    author_str = " and ".join(authors) if authors else "{Unknown Author}"
    
    # Get journal/venue safely
    primary_location = paper.get("primary_location")
    source = primary_location.get("source") if primary_location and isinstance(primary_location, dict) else None
    venue = source.get("display_name", "") if source and isinstance(source, dict) else ""
    
    if not venue:
        host_venue = paper.get("host_venue")
        venue = host_venue.get("display_name", "") if host_venue and isinstance(host_venue, dict) else ""
    
    # Get volume, pages, issue
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
    
    # Get abstract
    abstract = abstract_from_index(paper.get("abstract_inverted_index"))
    
    # Get keywords
    keywords = extract_keywords_from_paper(paper)
    
    # Get citation count
    citedby = paper.get("cited_by_count", 0)
    
    # Get OpenAlex ID
    openalex_id = paper.get("id", "") or ""
    
    # Generate key
    key = generate_bibtex_key(paper)
    
    # Determine entry type
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
    
    # Build BibTeX entry
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
    
    # Add abstract if requested (full abstract)
    if include_abstract and abstract:
        bib += f"  abstract  = {{{clean_bibtex_field(abstract)}}},\n"
    
    # Add full fields if requested
    if include_full:
        if keywords:
            bib += f"  keywords  = {{{clean_bibtex_field(', '.join(keywords))}}},\n"
        if citedby:
            bib += f"  citedby   = {{{citedby}}},\n"
        if openalex_id:
            bib += f"  openalex_id = {{{openalex_id}}},\n"
    
    # Add AI summary if requested
    if include_ai_summary:
        summary = generate_ai_summary(title, abstract, keywords)
        bib += f"  summary   = {{{clean_bibtex_field(summary)}}},\n"
    
    # Remove trailing comma and close
    bib = bib.rstrip(",\n") + "\n}"
    
    return bib

def get_cached_or_fetch(query, max_results, force_refresh=False):
    """Get data from cache or fetch fresh"""
    if not force_refresh:
        try:
            with open(CACHE_FILE, 'r') as f:
                cache = json.load(f)
                cache_time = datetime.fromisoformat(cache['timestamp'])
                if datetime.now() - cache_time < timedelta(hours=CACHE_MAX_AGE_HOURS):
                    if cache['query'] == query and cache['max_results'] == max_results:
                        print(f"📦 Using cached data from {cache_time.strftime('%Y-%m-%d %H:%M:%S')}")
                        return cache['data']
        except (FileNotFoundError, KeyError, json.JSONDecodeError):
            pass
    
    print("🌐 Fetching fresh data from OpenAlex...")
    data = fetch_all_papers(query, max_results)
    
    # Save to cache
    try:
        with open(CACHE_FILE, 'w') as f:
            json.dump({
                'timestamp': datetime.now().isoformat(),
                'query': query,
                'max_results': max_results,
                'data': data
            }, f)
    except Exception as e:
        print(f"⚠️  Could not save cache: {e}")
    
    return data

def fetch_all_papers(query, max_results=100):
    """Fetch papers with pagination support"""
    URL = "https://api.openalex.org/works"
    all_papers = []
    per_page = min(20, max_results)
    cursor = "*"
    
    with tqdm(total=max_results, desc="📚 Fetching papers") as pbar:
        while len(all_papers) < max_results:
            params = {
                "search": query,
                "per-page": per_page,
                "cursor": cursor
            }
            
            try:
                r = fetch_with_retry(URL, params=params)
                if not r:
                    break
                    
                data = r.json()
                results = data.get("results", [])
                
                if not results:
                    break
                
                all_papers.extend(results)
                pbar.update(len(results))
                
                cursor = data.get("meta", {}).get("next_cursor")
                if not cursor:
                    break
                    
            except Exception as e:
                print(f"\n❌ Error fetching papers: {e}")
                break
    
    return all_papers[:max_results]

# ==========================================================
# Output Generation
# ==========================================================

def create_output_directory():
    """Create the output directory if it doesn't exist"""
    Path(OUTPUT_DIR).mkdir(parents=True, exist_ok=True)
    return OUTPUT_DIR

def save_bib_files(papers, output_dir, include_full=True, include_ai_summary=True, include_abstract=True):
    """Generate clean and full BibTeX files"""
    clean_entries = []
    full_entries = []
    summary_entries = []
    abstract_entries = []  # New: entries with full abstracts
    
    print("\n📝 Processing papers...")
    for paper in tqdm(papers, desc="  Generating BibTeX"):
        try:
            # Clean entry (standard BibTeX)
            clean_bib = paper_to_bibtex(paper, include_full=False, include_ai_summary=False, include_abstract=False)
            clean_entries.append(clean_bib)
            
            # Abstract entry (with full abstract)
            abstract_bib = paper_to_bibtex(paper, include_full=False, include_ai_summary=False, include_abstract=True)
            abstract_entries.append(abstract_bib)
            
            # Full entry (with abstract, keywords, etc.)
            full_bib = paper_to_bibtex(paper, include_full=True, include_ai_summary=False, include_abstract=True)
            full_entries.append(full_bib)
            
            # Entry with AI summary
            summary_bib = paper_to_bibtex(paper, include_full=True, include_ai_summary=True, include_abstract=True)
            summary_entries.append(summary_bib)
            
        except Exception as e:
            print(f"⚠️  Error processing paper: {paper.get('title', 'Unknown')[:50]}... - {e}")
            continue
    
    # Save clean BibTeX
    clean_file = os.path.join(output_dir, "refs.bib")
    with open(clean_file, 'w', encoding='utf8') as f:
        for entry in clean_entries:
            f.write(entry)
            f.write("\n\n")
    print(f"  ✅ Clean BibTeX: {clean_file} ({len(clean_entries)} entries)")
    
    # Save abstract BibTeX (with full abstracts)
    abstract_file = os.path.join(output_dir, "refs_abstract.bib")
    with open(abstract_file, 'w', encoding='utf8') as f:
        for entry in abstract_entries:
            f.write(entry)
            f.write("\n\n")
    print(f"  ✅ Abstract BibTeX: {abstract_file} ({len(abstract_entries)} entries)")
    
    # Save full BibTeX
    full_file = os.path.join(output_dir, "refs_full.bib")
    with open(full_file, 'w', encoding='utf8') as f:
        for entry in full_entries:
            f.write(entry)
            f.write("\n\n")
    print(f"  ✅ Full BibTeX: {full_file} ({len(full_entries)} entries)")
    
    # Save BibTeX with AI summaries
    summary_file = os.path.join(output_dir, "refs_with_summary.bib")
    with open(summary_file, 'w', encoding='utf8') as f:
        for entry in summary_entries:
            f.write(entry)
            f.write("\n\n")
    print(f"  ✅ BibTeX with AI summaries: {summary_file} ({len(summary_entries)} entries)")
    
    return clean_file, abstract_file, full_file, summary_file

def save_json(papers, output_dir):
    """Save raw metadata as JSON"""
    json_file = os.path.join(output_dir, "papers.json")
    
    # Convert datetime objects to strings
    def json_serial(obj):
        if isinstance(obj, datetime):
            return obj.isoformat()
        raise TypeError(f"Type {type(obj)} not serializable")
    
    with open(json_file, 'w', encoding='utf8') as f:
        json.dump(papers, f, indent=2, default=json_serial)
    print(f"  ✅ JSON metadata: {json_file}")
    return json_file

def save_excel(papers, output_dir):
    """Save metadata as Excel for easy filtering"""
    excel_data = []
    
    for paper in papers:
        try:
            # Get authors
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
            
            # Get abstract
            abstract = abstract_from_index(paper.get("abstract_inverted_index"))
            
            # Get keywords
            keywords = extract_keywords_from_paper(paper)
            
            # Get journal safely
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
        except Exception as e:
            print(f"⚠️  Error processing paper for Excel: {e}")
            continue
    
    excel_file = os.path.join(output_dir, "papers.xlsx")
    if excel_data:
        df = pd.DataFrame(excel_data)
        df.to_excel(excel_file, index=False, engine='openpyxl')
        print(f"  ✅ Excel spreadsheet: {excel_file}")
    else:
        print("  ⚠️  No data for Excel spreadsheet")
    return excel_file

def save_abstracts_md(papers, output_dir):
    """Save readable abstracts in Markdown format"""
    md_file = os.path.join(output_dir, "abstracts.md")
    
    with open(md_file, 'w', encoding='utf8') as f:
        f.write("# Literature Review Notes\n\n")
        f.write(f"*Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}*\n\n")
        f.write(f"**Total Papers:** {len(papers)}\n\n")
        f.write("---\n\n")
        
        for i, paper in enumerate(papers, 1):
            try:
                # Get authors
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
                
                # Get abstract
                abstract = abstract_from_index(paper.get("abstract_inverted_index"))
                
                # Get keywords
                keywords = extract_keywords_from_paper(paper)
                
                # Get citation info
                citedby = paper.get("cited_by_count", 0)
                doi = paper.get("doi", "")
                
                # Get journal safely
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
                
                # AI-generated summary
                summary = generate_ai_summary(paper.get('title', ''), abstract, keywords)
                f.write("**AI Summary:**\n\n")
                f.write(f"> {summary}\n\n")
                
                f.write("---\n\n")
            except Exception as e:
                print(f"⚠️  Error processing paper for markdown: {e}")
                continue
    
    print(f"  ✅ Abstracts markdown: {md_file}")
    return md_file

def save_readme(papers_data, output_dir, query):
    """Save a README file describing the dataset"""
    readme_file = os.path.join(output_dir, "README.md")
    
    with open(readme_file, 'w', encoding='utf8') as f:
        f.write("# OpenAlex Paper Collection\n\n")
        f.write(f"**Search Query:** `{query}`\n\n")
        f.write(f"**Total Papers:** {len(papers_data)}\n\n")
        f.write(f"**Generated:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n")
        
        f.write("## File Descriptions\n\n")
        f.write("| File | Description |\n")
        f.write("|------|-------------|\n")
        f.write("| `refs.bib` | Clean BibTeX for Overleaf/LaTeX submission |\n")
        f.write("| `refs_abstract.bib` | BibTeX with full abstracts for literature review |\n")
        f.write("| `refs_full.bib` | Full metadata with abstracts, keywords, citation counts |\n")
        f.write("| `refs_with_summary.bib` | BibTeX with AI-generated summaries for LLM-assisted writing |\n")
        f.write("| `papers.xlsx` | Excel spreadsheet for easy filtering and sorting |\n")
        f.write("| `papers.json` | Complete OpenAlex metadata in JSON format |\n")
        f.write("| `abstracts.md` | Readable literature review notes with abstracts |\n\n")
        
        f.write("## Usage\n\n")
        f.write("### For Overleaf/LaTeX\n")
        f.write("Upload `refs.bib` to Overleaf. This contains only standard BibTeX fields.\n\n")
        
        f.write("### For Literature Review with Abstracts\n")
        f.write("Use `refs_abstract.bib` when you want to see abstracts in your bibliography.\n")
        f.write("This is useful for:\n")
        f.write("- Quickly scanning paper contributions\n")
        f.write("- Creating annotated bibliographies\n")
        f.write("- Sharing with collaborators who need context\n\n")
        
        f.write("### For Literature Review\n")
        f.write("- Open `abstracts.md` in your markdown editor for quick reading\n")
        f.write("- Use `papers.xlsx` to filter and sort papers\n")
        f.write("- Search `papers.json` for specific metadata\n\n")
        
        f.write("### For AI-Assisted Writing\n")
        f.write("Use `refs_with_summary.bib` with Aider or other LLM tools. The `summary` field contains concise descriptions that help the AI decide which papers to cite.\n\n")
        
        f.write("## Citation Counts\n\n")
        # Add top cited papers
        # Filter papers that have citation count
        papers_with_citations = [p for p in papers_data if p.get('cited_by_count') is not None]
        if papers_with_citations:
            sorted_papers = sorted(papers_with_citations, key=lambda x: x.get('cited_by_count', 0), reverse=True)
            f.write("**Top 5 Most Cited Papers:**\n\n")
            for i, paper in enumerate(sorted_papers[:5], 1):
                title = paper.get('title', 'Untitled')
                cited = paper.get('cited_by_count', 0)
                f.write(f"{i}. **{title}** ({cited} citations)\n")
        else:
            f.write("*No citation data available*\n")
        
        f.write("\n## Notes\n\n")
        f.write("- Generated using OpenAlex API\n")
        f.write("- Abstracts are reconstructed from inverted index\n")
        f.write("- AI summaries are automatically generated from abstracts\n")
        f.write("- Papers with missing metadata are gracefully handled\n")
    
    print(f"  ✅ README: {readme_file}")
    return readme_file

# ==========================================================
# Main Function
# ==========================================================

def main():
    parser = argparse.ArgumentParser(
        description="Fetch academic papers from OpenAlex and generate multiple outputs for literature review"
    )
    parser.add_argument(
        "--query", 
        help="Search query (overrides KEYWORDS)",
        type=str
    )
    parser.add_argument(
        "--max", 
        type=int, 
        default=MAX_RESULTS,
        help=f"Maximum number of papers to fetch (default: {MAX_RESULTS})"
    )
    parser.add_argument(
        "--output-dir",
        default=OUTPUT_DIR,
        help=f"Output directory name (default: {OUTPUT_DIR})"
    )
    parser.add_argument(
        "--force-refresh",
        action="store_true",
        help="Force refresh from OpenAlex, ignore cache"
    )
    parser.add_argument(
        "--no-excel",
        action="store_true",
        help="Skip Excel generation (requires pandas/openpyxl)"
    )
    parser.add_argument(
        "--keywords",
        nargs="+",
        help="Override default keywords (e.g., --keywords HVAC YOLO occupancy)"
    )
    args = parser.parse_args()
    
    # Determine query
    if args.query:
        query = args.query
    elif args.keywords:
        query = " ".join(args.keywords)
    else:
        query = " ".join(KEYWORDS)
    
    print("\n" + "=" * 80)
    print("📚 OPENALEX PAPER FETCHER")
    print("=" * 80)
    print(f"Search Query: {query}")
    print(f"Max Results: {args.max}")
    print(f"Output Directory: {args.output_dir}")
    print("-" * 80 + "\n")
    
    # Create output directory
    output_dir = create_output_directory()
    
    # Fetch papers (with caching)
    papers = get_cached_or_fetch(query, args.max, args.force_refresh)
    
    if not papers:
        print("❌ No papers found!")
        return
    
    print(f"\n📊 Found {len(papers)} papers\n")
    
    # Generate all outputs
    print("📝 Generating output files...")
    
    # Save BibTeX files
    clean_file, abstract_file, full_file, summary_file = save_bib_files(
        papers, 
        output_dir, 
        include_full=True, 
        include_ai_summary=True,
        include_abstract=True
    )
    
    # Save JSON
    json_file = save_json(papers, output_dir)
    
    # Save Excel (if not skipped and dependencies available)
    if not args.no_excel:
        try:
            save_excel(papers, output_dir)
        except ImportError as e:
            print(f"⚠️  {e}. Skipping Excel generation.")
            print("   Install with: pip install pandas openpyxl")
        except Exception as e:
            print(f"⚠️  Error generating Excel: {e}")
    else:
        print("⏭️  Skipping Excel generation")
    
    # Save abstracts as Markdown
    md_file = save_abstracts_md(papers, output_dir)
    
    # Save README
    readme_file = save_readme(papers, output_dir, query)
    
    # Summary
    print("\n" + "=" * 80)
    print("✅ COMPLETE! All files saved to:")
    print(f"   📁 {output_dir}/")
    print("-" * 80)
    print("📄 Generated Files:")
    print(f"   • refs.bib                - Clean bibliography for Overleaf")
    print(f"   • refs_abstract.bib       - With full abstracts for review")
    print(f"   • refs_full.bib           - Full metadata with abstracts")
    print(f"   • refs_with_summary.bib   - With AI summaries for Aider")
    print(f"   • papers.json             - Complete OpenAlex metadata")
    print(f"   • papers.xlsx             - Sortable/filterable spreadsheet")
    print(f"   • abstracts.md            - Readable literature review")
    print(f"   • README.md               - Documentation")
    print("=" * 80)
    print("\n💡 Quick Start:")
    print(f"   1. Upload {output_dir}/refs.bib to Overleaf")
    print(f"   2. Use {output_dir}/refs_abstract.bib for review")
    print(f"   3. Use {output_dir}/abstracts.md for literature review")
    print(f"   4. Use {output_dir}/refs_with_summary.bib with Aider")
    print()

if __name__ == "__main__":
    main()