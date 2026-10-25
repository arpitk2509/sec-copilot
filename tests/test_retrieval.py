from sec_copilot.rag.retrieval import build_tsquery, reciprocal_rank_fusion


def test_tsquery_joins_unique_words_with_or_and_ignores_punctuation():
    assert build_tsquery("What risks does Apple face? Apple's risks!") == "what | risks | does | apple | face | s"


def test_tsquery_of_nothing_is_empty():
    assert build_tsquery("?!  ...") == ""


def test_fusion_rewards_items_found_by_both_searches():
    keyword = [10, 20, 30]
    vector = [30, 40, 10]
    ranked = [item for item, _ in reciprocal_rank_fusion([keyword, vector])]
    assert ranked[:2] == [10, 30]            # both lists contain these two
    assert set(ranked) == {10, 20, 30, 40}


def test_fusion_scores_follow_the_formula():
    scores = dict(reciprocal_rank_fusion([[1, 2], [2]], k=60))
    assert scores[1] == 1 / 61
    assert scores[2] == 1 / 62 + 1 / 61


def test_fusion_of_nothing_is_empty():
    assert reciprocal_rank_fusion([[], []]) == []