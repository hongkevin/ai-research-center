"""추천 채널 (D75).

**고르게 하되 실측한 것만 준다.** 지키는 것 셋:

  1. 사칭·허구·시체는 `BLOCKED`에 있고 추천에 안 나온다
  2. 왜 뺐는지가 남는다 — 안 남기면 다음에 같은 시체를 다시 줍는다
  3. 증권사가 규모보다 앞이다 — 46,000명 익명 채널보다 205명 담당자다
"""

from __future__ import annotations

from arc.data.tg_channels import BLOCKED, RECOMMENDED, blocked_reason, recommended_for


class TestList:
    def test_nothing_recommended_is_also_blocked(self):
        """**두 목록이 겹치면 안 된다.** 권하면서 막는 것은 버그다."""
        names = {c.username.lower() for c in RECOMMENDED}
        blocked = {k.lower() for k in BLOCKED}
        assert names & blocked == set()

    def test_usernames_are_unique(self):
        names = [c.username.lower() for c in RECOMMENDED]
        assert len(names) == len(set(names))

    def test_the_impersonator_is_blocked_with_a_reason(self):
        """**사칭은 단순 사망과 다르다.**

        `@nhsemicon`은 NH 반도체 채널로 보이지만 실체는 구독자 2명짜리
        그룹이고, 유명 채널 username들을 방 이름에 나열해 검색에 걸리게
        만들었다. 이게 목록에 들어가면 그 뒤로는 우리가 그걸 신뢰할 만한
        출처처럼 다루게 된다.
        """
        reason = blocked_reason("nhsemicon")
        assert "사칭" in reason
        assert blocked_reason("@nhsemicon") == reason  # @가 붙어도 같다

    def test_every_block_says_why(self):
        assert all(len(v) > 10 for v in BLOCKED.values())

    def test_a_clean_name_is_not_blocked(self):
        assert blocked_reason("merITz_tech") == ""

    def test_unverified_affiliation_is_marked(self):
        """**단정하지 않은 것은 단정하지 않는다고 적는다.**

        이니셜과 링크 정황만으로 증권사 채널이라고 말할 수 없다.
        """
        row = next(c for c in RECOMMENDED if c.username == "ejpark3312")
        assert "미확인" in row.note
        assert row.kind != "broker"


class TestOrder:
    def test_a_broker_beats_a_bigger_anonymous_channel(self):
        """205명짜리 담당 애널리스트가 46,000명 익명 채널보다 먼저다."""
        order = [c.username for c in recommended_for([])]
        assert order.index("TechInventory") < order.index("bornlupin")

    def test_my_sector_comes_first_of_all(self):
        order = recommended_for(["화장품"])
        assert order[0].sector == "화장품"

    def test_a_sector_mismatch_is_not_dropped(self):
        """**빼지 않는다** — 내 섹터 밖 채널이 쓸모없는 것은 아니다."""
        assert len(recommended_for(["화장품"])) == len(RECOMMENDED)

    def test_no_sectors_still_orders_brokers_first(self):
        assert recommended_for(None)[0].kind == "broker"
