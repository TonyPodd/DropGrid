from collections import Counter
from uuid import UUID

import pytest

from dropgrid.db.models import Account, Community, Submission
from dropgrid.domain.enums import AccountStatus
from dropgrid.services.account_pools import distribute


@pytest.mark.parametrize("count,unassigned", [(250, 0), (314, 14)])
def test_balanced_operational_capacity(count, unassigned):
    accounts = [
        (
            Account(
                id=UUID(int=i),
                name=str(i),
                vk_user_id=i,
                encrypted_access_token="mock",
                status=AccountStatus.active,
            ),
            100,
            i,
        )
        for i in (1, 2, 3)
    ]
    rows = [(Submission(id=UUID(int=i + 1000)), Community(domain=f"club{i}")) for i in range(count)]
    allocation, errors = distribute(rows, accounts)
    assert len(errors) == unassigned
    assert max(Counter(allocation.values()).values()) <= 100
    assert distribute(list(reversed(rows)), accounts)[0] == allocation
