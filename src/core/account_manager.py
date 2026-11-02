"""Account management module."""

import logging
from typing import Optional

try:
    from src.models.account import Account
    from src.database.repository import AccountRepository
except ModuleNotFoundError:  # pragma: no cover - direct script execution fallback
    from models.account import Account
    from database.repository import AccountRepository

logger = logging.getLogger(__name__)


class AccountManager:
    """Manages game accounts and their state."""
    
    def __init__(self, repo: AccountRepository):
        self.repo = repo
        self._accounts: dict[str, Account] = {}  # account_id -> Account

    def load_from_database(self) -> list[Account]:
        """Load persisted accounts from the database into memory."""
        rows = self.repo.get_all("accounts")
        self._accounts = {}
        for row in rows:
            account = Account(
                account_id=str(row.get("account_id") or row.get("id")),
                username=row.get("username", ""),
                world=row.get("world", ""),
                server=row.get("server", ""),
                alliance=row.get("alliance"),
            )
            self._accounts[account.account_id] = account
        return list(self._accounts.values())
    
    def add_account(
        self,
        account_id: str,
        username: str,
        world: str,
        server: str,
        alliance: str = None,
    ) -> Account:
        """Create a new game account."""
        account = Account(
            account_id=account_id,
            username=username,
            world=world,
            server=server,
            alliance=alliance,
        )
        self._accounts[account_id] = account
        
        # Save to database
        self.repo.insert("accounts", {
            "account_id": account_id,
            "username": username,
            "world": world,
            "server": server,
            "alliance": alliance,
        })
        
        logger.info(f"Account added: {username} on {world}/{server}")
        return account
    
    def get_account(self, account_id: str) -> Optional[Account]:
        """Get an account by its generated account_id, or fall back to username lookup."""
        if account_id in self._accounts:
            return self._accounts[account_id]

        data = self.repo.get_by_account_id(account_id)
        if data is None:
            data = self.repo.get_by_username(account_id)

        if data:
            resolved_id = str(data.get("account_id") or data.get("id"))
            account = Account(
                account_id=resolved_id,
                username=data["username"],
                world=data["world"],
                server=data["server"],
                alliance=data.get("alliance"),
            )
            self._accounts[resolved_id] = account
            return account

        return None
    
    def get_all_accounts(self) -> list[Account]:
        """Get all registered accounts."""
        if not self._accounts:
            # Load from database
            rows = self.repo.get_all("accounts")
            for row in rows:
                account = Account(
                    account_id=str(row["id"]),
                    username=row["username"],
                    world=row["world"],
                    server=row["server"],
                    alliance=row.get("alliance"),
                )
                self._accounts[str(row["id"])] = account
        
        return list(self._accounts.values())
    
    def update_last_login(self, account_id: str) -> None:
        """Update the last login timestamp."""
        if account_id in self._accounts:
            self._accounts[account_id].update_last_login()
        
        self.repo.update_last_login(account_id)
    
    def remove_account(self, account_id: str) -> bool:
        """Remove an account."""
        if account_id in self._accounts:
            del self._accounts[account_id]
            return self.repo.delete("accounts", "id = :id", {"id": account_id})
        return False
