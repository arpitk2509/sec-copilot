from sec_copilot.ingest.sections import extract_section, html_to_text

FILLER = "The company faces many risks that could affect its results and financial condition. " * 60


def make_filing(heading_html: str) -> str:
    """A small fake 10-K. heading_html builds the headings, so we can test different HTML styles."""
    h = heading_html
    return f"""
    <html><body>
    <ix:header><p>Item 1A. Risk Factors hidden metadata</p></ix:header>
    <table>
      <tr><td>Item 1.</td><td>Business</td><td>3</td></tr>
      <tr><td>Item 1A.</td><td>Risk Factors</td><td>5</td></tr>
      <tr><td>Item 1B.</td><td>Unresolved Staff Comments</td><td>17</td></tr>
      <tr><td>Item 7.</td><td>Management's Discussion and Analysis</td><td>20</td></tr>
      <tr><td>Item 7A.</td><td>Quantitative and Qualitative Disclosures About Market Risk</td><td>30</td></tr>
    </table>
    {h('Item 1. Business')}
    <p>We describe our business here. For risks, see Part I, Item 1A. Risk Factors of this report, and
    <span>Item 1A. Risk Factors</span> below.</p>
    {h('Item 1A. Risk Factors')}
    <p>RISKFACTORS_START {FILLER}</p>
    <p>As discussed in Item 1A. Risk Factors, the supply chain could be disrupted. {FILLER}</p>
    <p>RISKFACTORS_END</p>
    {h('Item 1B. Unresolved Staff Comments')}
    <p>None.</p>
    {h('Item 7. Management&#8217;s Discussion and Analysis of Financial Condition and Results of Operations')}
    <p>MDA_START {FILLER}</p><p>MDA_END</p>
    {h('Item 7A. Quantitative and Qualitative Disclosures About Market Risk')}
    <p>Interest rate risk.</p>
    </body></html>
    """


def plain_heading(text):
    return f"<p><b>{text}</b></p>"


def split_span_heading(text):
    """Headings where 'Item 1A.' and the title sit in separate spans, with non-breaking spaces."""
    number, _, rest = text.replace("Item ", "").partition(". ")
    return f"<div><span>Item&nbsp;{number}.</span><span>&nbsp;{rest}</span></div>"


def table_heading(text):
    number, _, rest = text.replace("Item ", "").partition(". ")
    return f"<table><tr><td>Item {number}.</td><td>{rest}</td></tr></table>"


def check(html):
    text = html_to_text(html)
    risk = extract_section(text, "Item 1A")
    mda = extract_section(text, "Item 7")
    assert risk is not None and mda is not None
    assert "RISKFACTORS_START" in risk and "RISKFACTORS_END" in risk
    assert "MDA_START" not in risk and "We describe our business" not in risk   # no leaking in from Item 1
    assert "Unresolved Staff Comments" not in risk.split("RISKFACTORS_END")[1]
    assert "MDA_START" in mda and "MDA_END" in mda
    assert "RISKFACTORS_START" not in mda
    assert "hidden metadata" not in text


def test_plain_headings():
    check(make_filing(plain_heading))


def test_headings_split_across_spans():
    check(make_filing(split_span_heading))


def test_headings_in_table_cells():
    check(make_filing(table_heading))


def test_uppercase_headings():
    check(make_filing(lambda t: plain_heading(t.upper())))


def test_pointer_to_annual_report_is_not_a_section():
    html = f"""<html><body>
    <p>Item 7. Management's Discussion and Analysis</p>
    <p>Incorporated by reference to the annual report.</p>
    <p>Item 7A. Quantitative and Qualitative Disclosures About Market Risk</p>
    <p>{FILLER}</p></body></html>"""
    assert extract_section(html_to_text(html), "Item 7") is None


def test_missing_section_returns_none():
    assert extract_section(html_to_text("<html><body><p>nothing here</p></body></html>"), "Item 1A") is None


def test_cross_reference_at_start_of_a_block_in_business_section():
    """A paragraph in Item 1 that happens to begin with 'Item 1A. Risk Factors' must not stretch the section."""
    html = make_filing(plain_heading).replace(
        "<p>We describe our business here.",
        "<p>BUSINESS_TAIL_MARKER</p><p>Item 1A. Risk Factors describes our risks in detail.</p><p>We describe our business here.",
    )
    text = html_to_text(html)
    risk = extract_section(text, "Item 1A")
    assert "RISKFACTORS_START" in risk
    assert "BUSINESS_TAIL_MARKER" not in risk
    assert "We describe our business" not in risk


def test_line_breaks_in_html_source_do_not_become_headings():
    html = make_filing(plain_heading).replace("see Part I, Item 1A. Risk Factors of this report, and",
                                              "see Part I, and\n\n   Item 1A. Risk Factors\n of this report, and")
    text = html_to_text(html)
    risk = extract_section(text, "Item 1A")
    lines = risk.splitlines()
    assert lines[0].startswith("Item 1A. Risk Factors")
    assert lines[1].startswith("RISKFACTORS_START")



def test_xml_declaration_at_start_does_not_warn():
    import warnings

    html = '<?xml version="1.0" encoding="utf-8"?>' + make_filing(plain_heading)
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        text = html_to_text(html)
    assert extract_section(text, "Item 1A") is not None



def test_end_heading_wording_can_differ():
    """Some filings say 'Qualitative and Quantitative' instead of the usual order."""
    html = f"""<html><body>
    <p>Item 7. Management's Discussion and Analysis</p><p>MDA_START {FILLER}</p><p>MDA_END</p>
    <p>Item 7A. Qualitative and Quantitative Disclosures About Market Risk</p><p>Interest rate risk.</p></body></html>"""
    mda = extract_section(html_to_text(html), "Item 7")
    assert mda is not None and "MDA_END" in mda and "Interest rate risk" not in mda


def test_section_ends_at_item_8_when_there_is_no_item_7a():
    html = f"""<html><body>
    <p>Item 7. Management's Discussion and Analysis</p><p>MDA_START {FILLER}</p><p>MDA_END</p>
    <p>Item 8. Consolidated Financial Statements and Supplementary Data</p><p>Balance sheet.</p></body></html>"""
    mda = extract_section(html_to_text(html), "Item 7")
    assert mda is not None and "MDA_END" in mda and "Balance sheet" not in mda



def test_table_cells_with_paragraphs_stay_on_one_line_per_row():
    """Some filings put every number in its own <div> inside a cell. A row must still be one line."""
    html = """<html><body><table>
      <tr><td><div>Revenue</div></td><td><div>$</div></td><td><div>101,832</div></td><td><div>$</div></td><td><div>90,000</div></td></tr>
      <tr><td><p>Net income</p></td><td><p>$</p></td><td><p>(1,646</p></td><td><p>)</p></td></tr>
    </table><p>A normal paragraph.</p></body></html>"""
    lines = html_to_text(html).splitlines()
    assert lines[0] == "Revenue $ 101,832 $ 90,000"
    assert lines[1] == "Net income $ (1,646 )"
    assert lines[2] == "A normal paragraph."