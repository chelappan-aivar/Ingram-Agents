import pytest

from diagram_generator.harness.quotes import normalise, quote_found

TEXT = (
    "The Orders API runs on AWS Lambda. Requests arrive\nthrough Amazon API Gateway\n"
    "• Orders are written to Amazon DynamoDB\n"
    "- Events go to the customer’s “notification” queue – Amazon SQS.\n"
    "The deploy-\nment target is us-east-1."
)


@pytest.mark.parametrize("quote", [
    "the orders api runs on aws lambda",                       # case
    "Requests arrive through Amazon API Gateway",              # newline vs space
    "Requests   arrive \n through  Amazon API Gateway",        # extra whitespace
    "Orders are written to Amazon DynamoDB",                   # after a bullet
    "• Orders are written to Amazon DynamoDB",             # bullet in the quote
    "Events go to the customer's \"notification\" queue",      # curly -> straight
    "queue - Amazon SQS",                                      # en dash vs hyphen
    "queue — Amazon SQS",                                 # em dash vs en dash
    "The deployment target is us-east-1",                      # hyphenation break
    "The deploy-ment target",                                  # hyphen kept in quote, broken in text
])
def test_found(quote):
    assert quote_found(quote, TEXT)


@pytest.mark.parametrize("quote", [
    "",
    "   ",
    "Orders are written to Amazon S3",
    "Amazon SQS Orders are written",                           # wrong order
])
def test_not_found(quote):
    assert not quote_found(quote, TEXT)


def test_empty_text():
    assert not quote_found("anything", "")


def test_ellipsis_parts_in_order():
    assert quote_found("The Orders API ... AWS Lambda", TEXT)
    assert quote_found("The Orders API…AWS Lambda", TEXT)
    assert quote_found("Requests arrive ... Amazon DynamoDB ... Amazon SQS", TEXT)


def test_ellipsis_parts_out_of_order_or_missing():
    assert not quote_found("Amazon SQS ... The Orders API", TEXT)
    assert not quote_found("The Orders API ... Amazon S3", TEXT)


def test_ellipsis_parts_do_not_overlap():
    assert not quote_found("AWS Lambda ... AWS Lambda", TEXT)


def test_normalise_basics():
    assert normalise("  A’s – B\n C ") == "a's - b c"
