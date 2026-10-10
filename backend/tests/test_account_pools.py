from collections import Counter
from uuid import UUID

import pytest

from dropgrid.db.models import Account, Community, Submission
from dropgrid.domain.enums import AccountStatus, CategoryGender, GenderTag
from dropgrid.services.account_pools import CATEGORY_UNASSIGNED, distribute
from dropgrid.services.category_genders import Placement

MALE, FEMALE, UNISEX = CategoryGender.male, CategoryGender.female, CategoryGender.unisex


def account(i, gender=None, quota=100, priority=None):
    return (
        Account(
            id=UUID(int=i),
            name=str(i),
            vk_user_id=i,
            encrypted_access_token="mock",
            status=AccountStatus.active,
            gender_tag=gender,
        ),
        quota,
        i if priority is None else priority,
    )


def targets(spec):
    """spec: [(category, gender, count)] -> rows and placements keyed by community id."""
    rows, placements = [], {}
    for category, gender, count in spec:
        for _ in range(count):
            i = len(rows)
            community = Community(id=UUID(int=10_000 + i), domain=f"club{i:04}")
            rows.append((Submission(id=UUID(int=1_000 + i)), community))
            placements[community.id] = Placement(category, gender)
    return rows, placements


def categories(rows, placements, allocation, account_id):
    return [
        placements[c.id].category
        for row_id in allocation
        if allocation[row_id] == account_id
        for r, c in rows
        if r.id == row_id
    ]


@pytest.mark.parametrize("count,unassigned", [(250, 0), (314, 14)])
def test_operational_capacity(count, unassigned):
    accounts = [account(i) for i in (1, 2, 3)]
    rows, placements = targets([("ОБЩЕЕ", UNISEX, count)])
    allocation, errors = distribute(rows, accounts, placements)
    assert len(errors) == unassigned
    assert set(errors.values()) <= {"account_capacity_exhausted"}
    assert max(Counter(allocation.values()).values()) <= 100
    assert distribute(list(reversed(rows)), list(reversed(accounts)), placements)[0] == allocation


def test_accounts_are_filled_one_after_another_in_pool_order():
    rows, placements = targets([("ОБЩЕЕ", UNISEX, 250)])
    accounts = [account(1, priority=2), account(2, priority=0), account(3, priority=1)]
    allocation, errors = distribute(rows, accounts, placements)
    assert not errors
    assert list(dict.fromkeys(allocation.values())) == [UUID(int=2), UUID(int=3), UUID(int=1)]
    assert Counter(allocation.values()) == {UUID(int=2): 100, UUID(int=3): 100, UUID(int=1): 50}


def test_own_gender_categories_first_then_unisex_then_next_account():
    rows, placements = targets([("АВТО", MALE, 30), ("КРАСОТА", FEMALE, 40), ("ЮМОР", UNISEX, 100)])
    male, female = account(1, GenderTag.male), account(2, GenderTag.female)
    allocation, errors = distribute(rows, [male, female], placements)
    assert not errors and len(allocation) == 170
    assert categories(rows, placements, allocation, UUID(int=1)) == ["АВТО"] * 30 + ["ЮМОР"] * 70
    assert categories(rows, placements, allocation, UUID(int=2)) == ["КРАСОТА"] * 40 + ["ЮМОР"] * 30
    # The insertion order is the send order: the whole first account precedes the second.
    owners = list(allocation.values())
    assert owners == [UUID(int=1)] * 100 + [UUID(int=2)] * 70


def test_gendered_categories_never_go_to_other_or_unknown_gender_accounts():
    rows, placements = targets([("АВТО", MALE, 3), ("КРАСОТА", FEMALE, 2), ("ЮМОР", UNISEX, 2)])
    accounts = [account(1, None), account(2, GenderTag.unspecified), account(3, GenderTag.female)]
    allocation, errors = distribute(rows, accounts, placements)
    by_category = {placements[c.id].category: allocation.get(r.id) for r, c in rows}
    assert by_category["КРАСОТА"] == UUID(int=3)
    assert by_category["ЮМОР"] == UUID(int=1)
    assert set(errors.values()) == {"account_gender_mismatch"} and len(errors) == 3


def test_male_shortage_reports_capacity_and_unisex_fills_leftover_quota():
    rows, placements = targets([("АВТО", MALE, 120), ("ЮМОР", UNISEX, 50)])
    allocation, errors = distribute(
        rows, [account(1, GenderTag.male), account(2, GenderTag.female)], placements
    )
    assert Counter(allocation.values()) == {UUID(int=1): 100, UUID(int=2): 50}
    assert list(errors.values()) == ["account_capacity_exhausted"] * 20


def test_undistributed_category_is_never_allocated():
    rows, placements = targets([("АВТО", MALE, 2), ("НОВОЕ", None, 3)])
    rows.append((Submission(id=UUID(int=9)), Community(id=UUID(int=99), domain="outside")))
    allocation, errors = distribute(rows, [account(1, GenderTag.male)], placements)
    assert len(allocation) == 2
    assert list(errors.values()) == [CATEGORY_UNASSIGNED] * 4


def test_community_gender_tag_overrides_category():
    rows, placements = targets([("ЮМОР", UNISEX, 1), ("НОВОЕ", None, 1)])
    for _, community in rows:
        community.required_gender_tag = GenderTag.female
    allocation, errors = distribute(rows, [account(1, GenderTag.male)], placements)
    assert not allocation
    assert set(errors.values()) == {"account_gender_mismatch"}
