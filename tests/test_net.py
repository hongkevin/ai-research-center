"""어느 주소로 열 것인가 (D88).

이 파일이 지키는 것 하나: **못 찾았을 때 `0.0.0.0`으로 떨어지지 않는다.**

폰에서 개발 화면을 보려면 서버가 루프백 밖으로 나와야 하는데, 흔한 처방인
`--host 0.0.0.0`은 **모든 인터페이스**를 연다. 카페 WiFi에서 그러면 그 망의
아무나 DART·금융위·ECOS·LLM 키를 쓴다. tailnet 주소 하나에만 바인딩하면
그 주소는 내 tailnet 안에서만 라우팅되므로 같은 망에 있어도 도달 경로가 없다.

「편의를 위한 폴백」이 그 성질을 통째로 없앤다 — 그래서 못 찾으면 멈춘다.
"""

from __future__ import annotations

from arc.net import TAILNET, is_tailnet, resolve_host


class TestWhatCountsAsTailnet:
    def test_the_cgnat_range_is_the_whole_rule(self):
        """Tailscale은 `100.64.0.0/10`을 쓴다 — 공인 인터넷에 안 실린다."""
        assert is_tailnet("100.64.0.1")
        assert is_tailnet("100.127.255.254")
        assert is_tailnet("100.101.102.103")

    def test_it_does_not_fall_for_a_lookalike(self):
        """**`100.`으로 시작한다고 tailnet이 아니다.**

        `100.0.0.1`·`100.200.0.1`은 그 대역 밖이고, 공인 IP일 수 있다.
        여기서 헐겁게 잡으면 「tailnet에만 열었다」는 말이 거짓이 된다.
        """
        assert not is_tailnet("100.0.0.1")
        assert not is_tailnet("100.63.255.255")
        assert not is_tailnet("100.128.0.1")
        assert not is_tailnet("100.200.0.1")

    def test_other_private_ranges_are_not_tailnet(self):
        """집 공유기(`10.x`·`192.168.x`)는 tailnet이 아니다 — 그게 문제의 출발점이다."""
        assert not is_tailnet("10.0.0.47")
        assert not is_tailnet("192.168.0.10")
        assert not is_tailnet("127.0.0.1")

    def test_garbage_does_not_crash(self):
        for bad in ("", "abc", "100.64", "100.64.0.0.1", "100.x.0.1"):
            assert not is_tailnet(bad)


class TestResolveHost:
    def test_an_explicit_address_is_left_alone(self):
        """`--host`에 직접 적은 것은 건드리지 않는다 — 사람이 정한 것이다."""
        assert resolve_host("127.0.0.1") == ("127.0.0.1", "")
        assert resolve_host("0.0.0.0") == ("0.0.0.0", "")

    def test_tailnet_resolves_when_present(self, monkeypatch):
        monkeypatch.setattr("arc.net.tailnet_address", lambda: "100.101.102.103")
        host, note = resolve_host(TAILNET)
        assert host == "100.101.102.103"
        assert "100.101.102.103" in note

    def test_a_missing_tailnet_refuses_instead_of_opening_everything(self, monkeypatch):
        """**여기가 이 모듈의 존재 이유다.**

        Tailscale이 꺼져 있을 때 `0.0.0.0`으로 떨어지면, 지키려던 성질이
        정확히 그 순간 사라진다 — 그리고 사람은 열린 줄 안다.
        """
        monkeypatch.setattr("arc.net.tailnet_address", lambda: "")
        host, note = resolve_host(TAILNET)
        assert host == "", "빈 주소를 내서 부르는 쪽이 멈춰야 한다"
        assert "Tailscale" in note
        assert "0.0.0.0" in note, "왜 그 처방을 쓰면 안 되는지 같이 말해야 한다"
