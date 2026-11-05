from typing import Dict, List, Literal, Optional
from pydantic import BaseModel, Field

LABELS = ["setup", "scheme", "confrontation", "humiliation", "reveal", "betrayal", "power_shift", "romance", "danger", "emotional_light"]
PRIORITY = {"setup": "low", "scheme": "medium", "confrontation": "high", "humiliation": "high", "reveal": "very_high",
            "betrayal": "very_high", "power_shift": "very_high", "romance": "medium", "danger": "high", "emotional_light": "low"}
HOOK_TYPES = ["slap_violence", "public_humiliation", "kiss_closeness", "shocking_line", "threat", "contract_deal", "identity_reveal",
              "betrayal_caught", "secret_exposed", "underdog_flip", "revenge_moment", "in_media_res", "danger"]
ROLES = ["hook", "escalation", "payoff", "cliffhanger"]
TEMPLATES = ["single_scene", "reveal_tease", "mini_story", "underdog_revenge", "romance_tension", "montage", "hook_ending", "ending_scene"]

Label = Literal["setup", "scheme", "confrontation", "humiliation", "reveal", "betrayal", "power_shift", "romance", "danger", "emotional_light"]
HookType = Literal["slap_violence", "public_humiliation", "kiss_closeness", "shocking_line", "threat", "contract_deal", "identity_reveal",
                   "betrayal_caught", "secret_exposed", "underdog_flip", "revenge_moment", "in_media_res", "danger"]
Template = Literal["single_scene", "reveal_tease", "mini_story", "underdog_revenge", "romance_tension", "montage"]


# step 2
class KeyEvent(BaseModel):
    event_id: str = ""
    line_id: str
    description: str


class LineRef(BaseModel):
    line_id: str
    description: str


class EpisodeOut(BaseModel):
    episode: int
    summary: str
    characters: List[str]
    key_events: List[KeyEvent]
    ending_cliffhanger: LineRef
    primary_label: Label
    secondary_labels: List[Label] = Field(default_factory=list)
    peak_line_id: str
    label_reasons: Dict[str, str] = Field(default_factory=dict)


# step 3
class Character(BaseModel):
    char_id: str
    name: str
    aliases: List[str] = Field(default_factory=list)
    role: str = ""
    relationships: List[str] = Field(default_factory=list)


class Thread(BaseModel):
    thread_id: str
    name: str
    start_episode: int
    resolution_episode: Optional[int] = None
    status: str = "open"
    key_event_ids: List[str] = Field(default_factory=list)


class Twist(BaseModel):
    twist_id: str
    description: str
    severity: Literal["major", "minor"]
    setup_event_id: Optional[str] = None
    payoff_event_id: Optional[str] = None


class Link(BaseModel):
    setup_event_id: str
    payoff_event_id: str
    thread_id: Optional[str] = None


class Bible(BaseModel):
    genre_tone: str
    characters: List[Character]
    threads: List[Thread]
    twists: List[Twist] = Field(default_factory=list)
    setup_payoff_links: List[Link] = Field(default_factory=list)


# step 4
class Shot(BaseModel):
    shot_id: str = ""
    size: Literal["long", "medium", "small"]
    start_line_id: str
    end_line_id: str
    key_line_id: str
    role: Literal["hook", "escalation", "payoff", "cliffhanger"]
    hook_type: Optional[HookType] = None
    intensity: int = Field(ge=1, le=10)
    needs_context: Literal["low", "medium", "high"]
    characters: List[str] = Field(default_factory=list)
    thread_id: Optional[str] = None
    quote_or_action: str


class ShotsOut(BaseModel):
    episode: int
    shots: List[Shot]


# step 5a
class Beat(BaseModel):
    beat: Literal["hook", "escalation", "payoff", "cliffhanger", "peak", "scene"]
    need: str
    event_id: str
    thread_id: Optional[str] = None


class Script(BaseModel):
    script_id: str
    template: Template
    idea: str
    beats: List[Beat]


class ScriptsOut(BaseModel):
    scripts: List[Script]


# step 5b
class Pick(BaseModel):
    beat_index: int
    shot_id: str


class MatchOut(BaseModel):
    script_id: str
    picks: List[Pick]


# step 7
class FullScore(BaseModel):
    promo_id: str
    hook: int = Field(ge=1, le=10)
    cliffhanger: int = Field(ge=1, le=10)
    coherence: int = Field(ge=1, le=10)
    clarity: int = Field(ge=1, le=10)
    why_it_works: str
    hook_text: str
    cliffhanger_text: str
    safety_flags: List[str] = Field(default_factory=list)


class FullOut(BaseModel):
    scores: List[FullScore]


class FreshScore(BaseModel):
    promo_id: str
    clarity: int = Field(ge=1, le=10)
    would_watch_next: int = Field(ge=1, le=10)
    confusion: str


class FreshOut(BaseModel):
    scores: List[FreshScore]
