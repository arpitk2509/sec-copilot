from sec_copilot.ingest.chunking import (
    build_chunks,
    chunk_text,
    find_page_furniture,
    strip_page_furniture,
)


def test_removes_repeated_page_footers_and_page_numbers():
    text = "\n".join([
        "Risk one is described here.",
        "Apple Inc. | 2025 Form 10-K | 7",
        "Risk two is described here.",
        "Apple Inc. | 2025 Form 10-K | 8",
        "Table of Contents",
        "Risk three.",
        "Apple Inc. | 2025 Form 10-K | 9",
        "Table of Contents",
        "Table of Contents",
        "12", "13", "14",
    ])
    cleaned = strip_page_furniture(text, find_page_furniture(text))
    assert cleaned.splitlines() == ["Risk one is described here.", "Risk two is described here.", "Risk three."]


def test_keeps_a_single_mention_of_the_form_and_repeated_table_rows():
    text = "\n".join([
        "We describe this in our Annual Report on Form 10-K.",
        "Products $ 307,003 $ 300,000",
        "Services $ 98,000 $ 90,000",
        "Products $ 307,003 $ 300,000",
        "Products $ 307,003 $ 300,000",
        "Products $ 307,003 $ 300,000",
    ])
    furniture = find_page_furniture(text)
    assert furniture == set()
    assert strip_page_furniture(text, furniture) == text


def paragraphs(count, length=300):
    return "\n".join(f"P{i} " + "word " * (length // 5) for i in range(count))


def test_chunks_respect_size_and_cover_everything():
    text = paragraphs(40)
    chunks = chunk_text(text, max_chars=1500, overlap_chars=200)
    assert len(chunks) > 3
    assert all(0 < len(c) <= 1500 for c in chunks)
    for i in range(40):
        assert any(f"P{i} " in c for c in chunks)          # no paragraph is lost


def test_consecutive_chunks_overlap():
    chunks = chunk_text(paragraphs(40, length=150), max_chars=1500, overlap_chars=200)
    for before, after in zip(chunks, chunks[1:]):
        assert before.splitlines()[-1] == after.splitlines()[0]


def test_long_paragraph_is_split_at_sentences():
    text = " ".join(f"Sentence number {i} is here and it says something useful." for i in range(100))
    chunks = chunk_text(text, max_chars=500, overlap_chars=0)
    assert len(chunks) > 5
    assert all(len(c) <= 500 for c in chunks)
    assert all(c.endswith(".") for c in chunks)


def test_empty_text_gives_no_chunks():
    assert chunk_text("") == []
    assert chunk_text("\n  \n") == []


def test_build_chunks_adds_metadata_and_uses_our_fiscal_year():
    company = {"ticker": "NVDA", "cik": 1045810}
    filing = {"accession_no": "0001-25-000001", "period_end": "2025-01-26"}
    records = build_chunks(company, filing, {"Item 1A": paragraphs(10), "Item 7": paragraphs(5)})
    assert {r["section"] for r in records} == {"Item 1A", "Item 7"}
    assert all(r["fiscal_year"] == 2025 and r["ticker"] == "NVDA" for r in records)
    assert [r["chunk_index"] for r in records if r["section"] == "Item 7"] == list(range(
        len([r for r in records if r["section"] == "Item 7"])))


def test_furniture_is_found_across_the_whole_document_even_if_a_section_has_few_copies():
    document = "\n".join(["Intro text.", "Form 10-K | 1", "Section text A.", "Form 10-K | 2",
                          "Section text B.", "Form 10-K | 3", "Outro."])
    furniture = find_page_furniture(document)
    section = "Section text A.\nForm 10-K | 2\nSection text B."        # only ONE copy inside this section
    records = build_chunks({"ticker": "X", "cik": 1}, {"accession_no": "a", "period_end": "2025-12-31"},
                           {"Item 1A": section}, furniture)
    assert "10-K" not in records[0]["content"]



def test_exhibit_numbers_footnote_markers_and_exhibit_rows_are_not_furniture():
    text = "\n".join(["4.17", "4.30", "4.31", "(1)", "(2)", "(3)",
                      "10-K 3.1 8/7/20", "10-K 3.2 9/1/21", "10-K 4.5 1/2/22"])
    assert find_page_furniture(text) == set()


def test_page_number_styles_are_still_removed():
    text = "\n".join(["Text.", "- 12 -", "More text.", "- 13 -", "Even more.", "- 14 -",
                      "Page 1 of 90", "Page 2 of 90", "Page 3 of 90", "End."])
    cleaned = strip_page_furniture(text, find_page_furniture(text))
    assert cleaned.splitlines() == ["Text.", "More text.", "Even more.", "End."]



def test_pipe_style_footers_without_the_word_form_are_removed():
    text = "\n".join(["Risk text.", "31 | 2026 10-K", "More risk text.", "32 | 2026 10-K",
                      "Even more.", "33 | 2026 10-K", "End."])
    cleaned = strip_page_furniture(text, find_page_furniture(text))
    assert cleaned.splitlines() == ["Risk text.", "More risk text.", "Even more.", "End."]



def test_running_headers_without_numbers_are_removed_but_data_rows_and_rare_lines_stay():
    text = "\n".join(
        ["Real paragraph one."]
        + ["PART II", "CISCO SYSTEMS, INC."] * 8                     # repeated 8 times, no numbers: removed
        + ["Risk Factors Summary"] * 7                                 # only 7 times: kept
        + ["Total revenue $ 100 $ 90", "Total revenue $ 120 $ 80"] * 5   # data rows with numbers: kept
        + ["Real paragraph two."]
    )
    cleaned = strip_page_furniture(text, find_page_furniture(text)).splitlines()
    assert "PART II" not in cleaned and "CISCO SYSTEMS, INC." not in cleaned
    assert cleaned.count("Risk Factors Summary") == 7
    assert cleaned.count("Total revenue $ 100 $ 90") == 5
    assert cleaned[0] == "Real paragraph one." and cleaned[-1] == "Real paragraph two."


def test_annual_report_footers_with_page_numbers_are_removed():
    text = "\n".join(["Text.", "2025 Annual Report 23", "More.", "2025 Annual Report 24", "Even more.",
                      "2025 Annual Report 25", "End."])
    assert strip_page_furniture(text, find_page_furniture(text)).splitlines() == ["Text.", "More.", "Even more.", "End."]