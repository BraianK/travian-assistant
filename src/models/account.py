"""Account model for multi-account support."""

from datetime import datetime
from typing import Optional


class Account:
    """Represents a Travian Kingdoms game account."""
    
    def __init__(
        self,
        account_id: str = "",
        username: str = "",
        world: str = "",
        server: str = "",
        alliance: Optional[str] = None,
        created_at: Optional[datetime] = None,
        last_login: Optional[datetime] = None,
    ):
        self.account_id = account_id
        self.username = username
        self.world = world
        self.server = server
        self.alliance = alliance
        self.created_at = created_at or datetime.now()
        self.last_login = last_login or datetime.now()
    
    @property
    def is_active(self) -> bool:
        """Check if account has recent login activity."""
        if self.last_login is None:
            return False
        delta = datetime.now() - self.last_login
        return delta.days <= 7
    
    def update_last_login(self):
        """Update the last login timestamp."""
        self.last_login = datetime.now()
    
    def __repr__(self) -> str:
        return (
            f"Account(username='{self.username}', world='{self.world}', "
            f"server='{self.server}')"
        )
