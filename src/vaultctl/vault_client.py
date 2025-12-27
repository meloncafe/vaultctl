"""Vault API 클라이언트."""

from typing import Any, Optional

import httpx
from rich.console import Console

from .config import settings

console = Console()


class VaultError(Exception):
    """Vault API 오류."""

    def __init__(self, message: str, status_code: Optional[int] = None):
        self.message = message
        self.status_code = status_code
        super().__init__(self.message)


class VaultClient:
    """HashiCorp Vault API 클라이언트."""

    def __init__(
        self,
        addr: Optional[str] = None,
        token: Optional[str] = None,
        namespace: Optional[str] = None,
    ):
        self.addr = (addr or settings.vault_addr).rstrip("/")
        self.token = token or settings.vault_token
        self.namespace = namespace or settings.vault_namespace
        self._client: Optional[httpx.Client] = None

    @property
    def client(self) -> httpx.Client:
        """HTTP 클라이언트 (lazy initialization)."""
        if self._client is None:
            headers = {}
            if self.token:
                headers["X-Vault-Token"] = self.token
            if self.namespace:
                headers["X-Vault-Namespace"] = self.namespace

            self._client = httpx.Client(
                base_url=self.addr,
                headers=headers,
                verify=not settings.vault_skip_verify,
                timeout=30.0,
            )
        return self._client

    def _request(
        self,
        method: str,
        path: str,
        data: Optional[dict] = None,
        params: Optional[dict] = None,
    ) -> dict[str, Any]:
        """API 요청 실행."""
        try:
            response = self.client.request(
                method=method,
                url=f"/v1/{path}",
                json=data,
                params=params,
            )

            if response.status_code == 204:
                return {}

            result = response.json() if response.content else {}

            if response.status_code >= 400:
                errors = result.get("errors", [])
                error_msg = "; ".join(errors) if errors else f"HTTP {response.status_code}"
                raise VaultError(error_msg, response.status_code)

            return result

        except httpx.RequestError as e:
            raise VaultError(f"연결 실패: {e}") from e

    def close(self) -> None:
        """클라이언트 종료."""
        if self._client:
            self._client.close()
            self._client = None

    # ─────────────────────────────────────────────────────────────────────────
    # 인증 관련
    # ─────────────────────────────────────────────────────────────────────────

    def token_lookup(self) -> dict[str, Any]:
        """현재 토큰 정보 조회."""
        return self._request("GET", "auth/token/lookup-self")

    def token_renew(self, increment: Optional[int] = None) -> dict[str, Any]:
        """토큰 갱신."""
        data = {}
        if increment:
            data["increment"] = f"{increment}s"
        return self._request("POST", "auth/token/renew-self", data=data or None)

    def token_create(
        self,
        policies: list[str],
        ttl: Optional[str] = None,
        display_name: Optional[str] = None,
        no_default_policy: bool = True,
    ) -> dict[str, Any]:
        """새 토큰 생성."""
        data: dict[str, Any] = {
            "policies": policies,
            "no_default_policy": no_default_policy,
        }
        if ttl:
            data["ttl"] = ttl
        if display_name:
            data["display_name"] = display_name
        return self._request("POST", "auth/token/create", data=data)

    # ─────────────────────────────────────────────────────────────────────────
    # KV v2 Secrets Engine
    # ─────────────────────────────────────────────────────────────────────────

    def kv_get(self, mount: str, path: str) -> dict[str, Any]:
        """KV v2 시크릿 조회."""
        result = self._request("GET", f"{mount}/data/{path}")
        return result.get("data", {}).get("data", {})

    def kv_put(self, mount: str, path: str, data: dict[str, Any]) -> dict[str, Any]:
        """KV v2 시크릿 저장."""
        return self._request("POST", f"{mount}/data/{path}", data={"data": data})

    def kv_delete(self, mount: str, path: str) -> None:
        """KV v2 시크릿 삭제."""
        self._request("DELETE", f"{mount}/data/{path}")

    def kv_list(self, mount: str, path: str = "") -> list[str]:
        """KV v2 경로 목록 조회."""
        try:
            result = self._request("LIST", f"{mount}/metadata/{path}")
            return result.get("data", {}).get("keys", [])
        except VaultError as e:
            if e.status_code == 404:
                return []
            raise

    def kv_metadata(self, mount: str, path: str) -> dict[str, Any]:
        """KV v2 메타데이터 조회."""
        result = self._request("GET", f"{mount}/metadata/{path}")
        return result.get("data", {})

    # ─────────────────────────────────────────────────────────────────────────
    # Policy 관리
    # ─────────────────────────────────────────────────────────────────────────

    def policy_list(self) -> list[str]:
        """정책 목록 조회."""
        result = self._request("LIST", "sys/policies/acl")
        return result.get("data", {}).get("keys", [])

    def policy_read(self, name: str) -> str:
        """정책 내용 조회."""
        result = self._request("GET", f"sys/policies/acl/{name}")
        return result.get("data", {}).get("policy", "")

    def policy_write(self, name: str, policy: str) -> None:
        """정책 저장."""
        self._request("PUT", f"sys/policies/acl/{name}", data={"policy": policy})

    def policy_delete(self, name: str) -> None:
        """정책 삭제."""
        self._request("DELETE", f"sys/policies/acl/{name}")

    # ─────────────────────────────────────────────────────────────────────────
    # 헬스체크
    # ─────────────────────────────────────────────────────────────────────────

    def health(self) -> dict[str, Any]:
        """서버 상태 확인."""
        try:
            response = self.client.get("/v1/sys/health")
            return response.json()
        except Exception:
            return {"initialized": False, "sealed": True}

    def is_authenticated(self) -> bool:
        """인증 상태 확인."""
        try:
            self.token_lookup()
            return True
        except VaultError:
            return False


# 전역 클라이언트 인스턴스
_client: Optional[VaultClient] = None


def get_client() -> VaultClient:
    """전역 Vault 클라이언트 반환."""
    global _client
    if _client is None:
        _client = VaultClient()
    return _client


def set_token(token: str) -> None:
    """토큰 설정 및 클라이언트 재생성."""
    global _client
    settings.vault_token = token
    if _client:
        _client.close()
    _client = VaultClient(token=token)
