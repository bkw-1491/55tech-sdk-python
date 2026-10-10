from conftest import PINNACLE, check

from fiftyfivetech import FiftyFiveTechClient


def test_bookmakers(client: FiftyFiveTechClient):
    """
    The catalogue lists the key's bookmakers with their freshness bounds, and filters by slug.
    """
    books = check(client.get_bookmakers()).bookmakers
    assert books and all(b.slug for b in books)
    one = check(client.get_bookmakers(bookmakers=[books[0].slug])).bookmakers
    assert [b.slug for b in one] == [books[0].slug]
    pinnacle = next((b for b in books if b.slug == PINNACLE), None)
    if pinnacle is not None:
        assert pinnacle.max_delay_live_in_sec is not None
