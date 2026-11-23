"""Gossip membership conflict resolver (state simulation only)."""

from .model import Cause, MemberRecord, Status
from .resolver import MergeResult, Resolver, merge_groups

__all__ = [
    "Cause",
    "MemberRecord",
    "Status",
    "MergeResult",
    "Resolver",
    "merge_groups",
]
