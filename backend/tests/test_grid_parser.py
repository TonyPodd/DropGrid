import pytest

from dropgrid.domain.grid_parser import normalize_vk_community_reference, parse_grid


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("vk.com/test", "test"),
        ("https://vk.com/test", "test"),
        ("https://vk.ru/test", "test"),
        ("test", "test"),
        ("@maybe_test", "maybe_test"),
        ("club225093922", "club225093922"),
        ("public106546949", "club106546949"),
        (" HTTPS://VK.COM/TeSt/ ", "test"),
        ("www.vk.ru/test", "test"),
        ("club000123", "club123"),
        ("14vk14", "14vk14"),
        ("123", "123"),
        ("vk.com/podslushano.selfie", "podslushano.selfie"),
    ],
)
def test_normalize(value: str, expected: str) -> None:
    assert normalize_vk_community_reference(value) == expected


@pytest.mark.parametrize(
    "value",
    [
        "",
        "https://example.com/test",
        "https://vk.com.evil/test",
        "foo bar",
        "https://vk.com/foo/posts",
        "https://vk.com/foo?token=secret",
        "club0",
        "https://user:password@vk.com/foo",
        "vk.com/",
        "https://vk.com/foo#bar",
        "ftp://vk.com/foo",
        "foo!",
        "@",
        "a" * 65,
        "club9223372036854775808",
    ],
)
def test_normalize_invalid(value: str) -> None:
    with pytest.raises(ValueError):
        normalize_vk_community_reference(value)


def test_grid_categories_indices_and_aliases() -> None:
    result = parse_grid("""ГРУЗОВИКИ

230 vk.com/lujbit
231 https://vk.com/dmisley

ЗНАКОМСТВА
232 vk.ru/example
233 club225093922
234 public106546949
235 @maybe_test
""")
    assert [(x.category, x.community) for x in result.items] == [
        ("ГРУЗОВИКИ", "lujbit"),
        ("ГРУЗОВИКИ", "dmisley"),
        ("ЗНАКОМСТВА", "example"),
        ("ЗНАКОМСТВА", "club225093922"),
        ("ЗНАКОМСТВА", "club106546949"),
        ("ЗНАКОМСТВА", "maybe_test"),
    ]
    assert not result.errors


def test_malformed_and_duplicate_lines_are_isolated() -> None:
    result = parse_grid(
        "foo\n1 https://evil.com/bar\n2 not a link!\n3 VK.COM/FOO\n4 club123\n5 public123\n6 good"
    )
    assert [x.community for x in result.items] == ["foo", "club123", "good"]
    assert [e.line for e in result.errors] == [2, 3, 4, 6]
    assert result.errors[2].message == "Duplicate community"


def test_empty_and_explicit_category() -> None:
    assert parse_grid(" \n\n").items == []
    result = parse_grid("# mixed case category\n1. test\n2) vk.ru/other")
    assert all(item.category == "mixed case category" for item in result.items)
    assert len(result.items) == 2


@pytest.mark.parametrize("separator", [" - ", " — ", " – ", "    "])
def test_grid_comment_preserved_without_semantics(separator):
    result = parse_grid(f"# МУЗЫКА\nvk.com/music.track124{separator}девушка с машиной")
    assert not result.errors
    assert result.items[0].comment == "девушка с машиной"
    assert result.items[0].community == "music.track124"


def test_real_grid_categories_glued_index_and_shorthand():
    r = parse_grid("""ВОЕННЫЕ
85 vk.com/in_the_distance - дембель
ДОБРОЕ УТРО
102 vk.com/pod69red -Д
МУЗЫКА
195 vk.com/public212062547 – девушка с машиной
БМВ Е34
449vk.com/belogorskiy_cartel
ПИТБУЛЬ/АМСТАФФ
243 vk.com/club171866203
ЦИТАТА
232 vk.com/14vk14
""")
    assert not r.errors
    assert [i.comment for i in r.items][:3] == ["дембель", "Д", "девушка с машиной"]
    assert r.items[3].category == "БМВ Е34"
    assert r.items[4].category == "ПИТБУЛЬ/АМСТАФФ"
    assert r.items[5].community == "14vk14"


@pytest.mark.parametrize(
    "url",
    [
        "vk.com/group?x=1 - note",
        "https://evil.com/group — note",
        "vk.com/group/posts — note",
        "https://vk.com@evil.com/group — note",
    ],
)
def test_comment_does_not_loosen_url_validation(url):
    assert parse_grid(url).errors
