"""Skill Loader exception types."""

from __future__ import annotations


class SkillLoaderError(Exception):
    """Base for Skill Loader errors."""


class SkillNotFoundError(SkillLoaderError):
    pass


class SkillFrontmatterError(SkillLoaderError):
    pass


class SkillDuplicateError(SkillLoaderError):
    """Same skill name resolved from more than one source."""
