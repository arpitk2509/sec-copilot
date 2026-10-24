# Data notes and known limitations

- **Source:** SEC EDGAR XBRL company facts (GAAP, as originally reported). Not restated, not non-GAAP.
- **Analysis window:** 2014 onward. Earlier years (2009-2013) have gaps from the early XBRL era.
- **Fiscal labels:** derived from period dates, because the SEC's own fiscal year/quarter labels are sometimes wrong.
- **Q4:** never reported on its own, so it is derived as full year minus Q1-Q3 and flagged `is_derived`.
- **Skipped derived quarters:** IBM Q4 2021 and Intuit Q4 2013. The full-year figure and the earlier quarters
  look to be on different bases after a restatement, so storing the difference would give a wrong number.
- **Missing metrics:** IBM has no operating income and Intuit has no gross profit under the standard tags.
  Salesforce revenue is missing for fiscal 2016-2018 because the tag may be only a component of total revenue.
- **Short history:** Broadcom starts in 2018 (current registrant), ServiceNow in 2012 (IPO).
- **Validation:** derived Q4 revenue and growth for Micron matched its earnings release; data-quality SQL checks
  live in `db/queries/`.
  - **Documents not loaded:** Intel's 10-K doesn't use standard "Item 1A / Item 7" headings in its body (the Item mapping
  is a page-number index at the end), so neither section is extracted. IBM's MD&A is incorporated by reference to its
  annual report, so only its Risk Factors are loaded. Both are handled by returning "not found" rather than storing junk.