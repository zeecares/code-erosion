from candidate import choose


def test_all_cases_and_fallback():
    words = "one two three four five six seven eight nine ten eleven".split()
    for offset, word in enumerate(words, 1):
        assert choose(word, 20) == 20 + offset
    assert choose("other", 20) == 20
