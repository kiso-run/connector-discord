"""Tests for message splitting logic."""
import pytest
from run import split_message

MAX = 2000


def test_short_message_unchanged():
    assert split_message("Hello, world!") == ["Hello, world!"]


def test_empty_message():
    assert split_message("") == [""]


def test_exactly_at_limit():
    msg = "a" * MAX
    result = split_message(msg)
    assert result == [msg]


def test_one_char_over_limit():
    msg = "a" * (MAX + 1)
    result = split_message(msg)
    assert len(result) == 2
    assert all(len(p) <= MAX for p in result)
    assert "".join(result) == msg


def test_split_at_paragraph_boundary():
    para1 = "a" * 1500
    para2 = "b" * 1500
    msg = para1 + "\n\n" + para2

    result = split_message(msg)

    assert len(result) == 2
    assert result[0] == para1
    assert result[1] == para2


def test_two_paragraphs_that_fit_together():
    para = "x" * 500
    msg = para + "\n\n" + para  # 1002 chars — fits in one message

    result = split_message(msg)

    assert len(result) == 1
    assert result[0] == msg


def test_hard_split_when_no_paragraphs():
    msg = "a" * 4500
    result = split_message(msg)

    assert len(result) == 3
    assert all(len(p) <= MAX for p in result)
    assert "".join(result) == msg


def test_hard_split_preserves_all_content():
    msg = "z" * 7777
    result = split_message(msg)

    assert all(len(p) <= MAX for p in result)
    assert "".join(result) == msg


def test_mixed_oversized_and_normal_paragraphs():
    """A very long paragraph followed by a short one."""
    long_para = "L" * 3000
    short_para = "S" * 100
    msg = long_para + "\n\n" + short_para

    result = split_message(msg)

    assert all(len(p) <= MAX for p in result)
    # Last part should be the short paragraph (or merged with previous)
    assert short_para in "".join(result)


def test_custom_max_len():
    msg = "a" * 10
    result = split_message(msg, max_len=4)

    assert result == ["aaaa", "aaaa", "aa"]
    assert "".join(result) == msg


def test_three_paragraphs_each_fitting_separately():
    para = "p" * 800  # 800 chars; 800+2+800=1602 fits, but 1602+2+800=2404 doesn't
    msg = para + "\n\n" + para + "\n\n" + para

    result = split_message(msg)

    assert all(len(p) <= MAX for p in result)
    # Content must be fully preserved (joined with \n\n)
    assert "\n\n".join(result) == msg or "".join(result) == msg.replace("\n\n", "")
    # Simple check: no data loss
    total = "".join(r.replace("\n\n", "") for r in result)
    assert total == para * 3
