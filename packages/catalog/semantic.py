"""Offline multilingual semantic recall over the committed asset catalog.

The default embedder is a deterministic, dependency-free stand-in used by CI.
Every response labels it as ``deterministic-stand-in`` and says that it is not
real-model evidence. A pluggable CPU multilingual embedder can be selected for
separate evidence runs; the gateway never downloads weights and CI never
claims a real model. Semantic scores only order the recall set: hard filters,
scope, exact identities, cursors, snapshots and response budgets are unchanged.
"""

from __future__ import annotations

import hashlib
import importlib
import importlib.metadata
import logging
import os
import re
import unicodedata
from dataclasses import dataclass

import numpy as np

LOGGER = logging.getLogger(__name__)

SEMANTIC_SCHEMA = "lw.catalog-semantic/1"
STANDIN_ID = "lw-hash-concept-standin"
STANDIN_VERSION = "1"
STANDIN_DIMENSION = 512
STANDIN_THRESHOLD = 0.18
STANDIN_MIN_COVERAGE = 0.25
REAL_THRESHOLD = 0.30
# Real models can be confident about a paraphrase with little literal overlap.
# Below this score a lexical-coverage guard keeps unrelated queries at zero.
REAL_COVERAGE_BYPASS = 0.45
MAX_DOCUMENT_CHARS = 2400

LATIN = re.compile(r"[a-z0-9]+")
CJK = re.compile(r"[\u3400-\u9fff]+")

STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "in", "into",
    "is", "it", "of", "on", "or", "that", "the", "this", "to", "with", "while",
    "during", "between", "used", "use", "using", "can", "should", "would", "keep",
    "keeps", "keeping", "set", "setting", "make", "makes", "made", "get", "gets",
    "的", "了", "和", "与", "在", "为", "是", "把", "将", "用", "用来", "用于",
    "可以", "能够", "一个", "一种", "及", "或", "中", "上", "下", "时", "后", "前",
    "到", "向", "从", "里", "内", "外", "并", "而", "其", "之", "等", "地", "得",
}

# Small, auditable bilingual concept bridge. It is model data for the
# deterministic stand-in, not a claim of general language understanding.
_CONCEPT_GROUPS = {
    "container": (
        "container", "containers", "box", "boxes", "bin", "bins", "bucket", "buckets",
        "basket", "baskets", "crate", "crates", "tote", "totes", "tray", "trays",
        "case", "cases", "pail", "pails", "vial", "vials", "bottle", "bottles",
        "canister", "canisters", "bowl", "容器", "箱", "盒", "桶", "筐", "篮",
        "托盘", "罐", "瓶", "杯", "盘",
    ),
    "storage": (
        "storage", "store", "stores", "storing", "stored", "stow", "stowing",
        "organize", "organizes", "organizing", "organization", "organizer",
        "收纳", "储存", "存放", "仓储", "整理", "储物", "存储", "收纳盒", "整理箱",
    ),
    "packaging": (
        "packaging", "package", "packages", "carton", "cartons", "shipping",
        "包装", "纸箱", "打包", "运输包装",
    ),
    "transport": (
        "transport", "transporting", "move", "moves", "moving", "movement",
        "carry", "carries", "carrying", "haul", "hauling", "cart", "carts",
        "trolley", "trolleys", "dolly", "pushcart", "roller", "conveyor",
        "搬运", "运输", "运送", "推车", "台车", "手推车", "转运", "移动", "输送",
        "滚筒", "托盘车", "地牛",
    ),
    "cleaning": (
        "cleaning", "clean", "cleans", "wash", "washing", "mop", "mopping",
        "scrub", "scrubbing", "wipe", "wiping", "squeegee", "dustpan", "broom",
        "清洁", "打扫", "清洗", "擦拭", "拖把", "刷洗", "刮水", "簸箕", "扫帚",
        "洗涤", "保洁",
    ),
    "waste": (
        "waste", "trash", "garbage", "recycling", "recycle", "disposal", "dispose",
        "refuse", "litter", "spill",
        "垃圾", "废物", "废弃物", "回收", "废料", "处理", "溢漏", "丢弃",
    ),
    "lab": (
        "lab", "laboratory", "experiment", "experiments", "experimental",
        "实验室", "实验", "试验",
    ),
    "furniture": (
        "furniture", "desk", "desks", "table", "tables", "bench", "benches",
        "workbench", "shelf", "shelves", "shelving", "cabinet",
        "cabinets", "chair", "chairs", "stool", "stools", "counter", "surface",
        "家具", "桌子", "工作台", "实验台", "货架", "柜", "架子", "椅子", "凳子",
        "台面", "桌面", "操作台",
    ),
    "instrument": (
        "instrument", "instruments", "device", "devices", "equipment", "apparatus",
        "machine", "machines", "instrumentation", "tool", "tools",
        "仪器", "设备", "装置", "机器", "工具", "器材",
    ),
    "measurement": (
        "measure", "measures", "measuring", "measurement", "weigh", "weighing",
        "weight", "scale", "balance", "precision", "calibration",
        "测量", "称量", "称重", "天平", "秤", "精密", "校准", "计量",
    ),
    "mixing": (
        "mix", "mixes", "mixing", "stir", "stirring", "vortex", "shaker", "blend",
        "混合", "搅拌", "振荡", "涡旋", "混匀",
    ),
    "heating": (
        "heat", "heating", "heated", "warm", "warming", "incubate", "incubating",
        "incubation", "temperature", "thermostat",
        "加热", "恒温", "培养", "保温", "温度", "温控",
    ),
    "cooling": (
        "cool", "cooling", "cold", "freeze", "freezing", "frozen", "freezer",
        "refrigerator", "fridge", "cryo", "cryogenic", "insulated", "cooler",
        "冷却", "冷冻", "低温", "冰箱", "冷藏", "冷冻柜", "保温", "保冷", "冷冻管",
    ),
    "centrifuge": (
        "centrifuge", "centrifuges", "centrifugation", "spin", "spinning", "separate",
        "separation", "separating",
        "离心", "离心机", "分离", "旋转",
    ),
    "microscope": (
        "microscope", "microscopes", "microscopy", "magnify", "magnifying",
        "显微镜", "显微", "放大",
    ),
    "pipette": (
        "pipette", "pipettes", "pipetting", "pipette", "tip", "tips", "dispense",
        "dispensing", "移液", "移液器", "枪头", "吸头", "移取", "滴管",
    ),
    "glassware": (
        "glassware", "beaker", "beakers", "flask", "flasks", "tube", "tubes",
        "cylinder", "cylinders", "petri", "dish", "dishes", "funnel", "funnels",
        "slide", "slides", "glass",
        "玻璃器皿", "烧杯", "烧瓶", "试管", "量筒", "培养皿", "漏斗", "载玻片",
        "玻璃", "锥形瓶", "试剂瓶",
    ),
    "safety": (
        "safety", "protective", "protection", "protect", "glove", "gloves",
        "goggle", "goggles", "glasses", "coat", "shield", "mask", "respirator",
        "extinguisher", "eyewash", "first", "aid", "ear", "muffs", "cone",
        "安全", "防护", "保护", "手套", "护目镜", "眼镜", "实验服", "白大褂",
        "面罩", "口罩", "灭火器", "洗眼", "急救", "耳罩", "安全锥", "警示",
    ),
    "electronics": (
        "electronic", "electronics", "electrical", "power", "supply", "voltage",
        "current", "signal", "waveform", "oscilloscope", "multimeter", "solder",
        "soldering", "cable", "battery", "sensor", "junction", "panel", "generator",
        "电源", "电子", "电气", "电压", "电流", "信号", "波形", "示波器", "万用表",
        "焊接", "电缆", "电池", "传感器", "接线", "控制面板", "信号发生器",
    ),
    "fixture": (
        "fixture", "clamp", "clamps", "stand", "stands", "mount", "mounting",
        "bracket", "rail", "pegboard", "holder", "vise", "jack", "base",
        "夹具", "支架", "夹钳", "夹紧", "安装", "固定", "导轨", "挂板", "工具架",
        "虎钳", "台钳", "升降台", "磁性", "底座",
    ),
    "sample": (
        "sample", "samples", "specimen", "specimens", "culture", "cultures",
        "样品", "样本", "标本", "培养物",
    ),
    "sterilization": (
        "sterilize", "sterilizing", "sterilization", "sterile", "autoclave",
        "steam", "disinfect", "disinfecting",
        "灭菌", "消毒", "无菌", "蒸汽", "高压灭菌",
    ),
    "labeling": (
        "label", "labels", "labeling", "barcode", "tag", "tags",
        "标签", "标记", "条码",
    ),
    "sealing": (
        "seal", "sealing", "film", "parafilm", "cover", "closing",
        "封口", "密封", "膜", "盖",
    ),
    "small": ("small", "compact", "mini", "tiny", "小型", "小", "紧凑", "微型"),
    "large": ("large", "big", "heavy", "大型", "大", "重型", "重"),
    "floor": ("floor", "ground", "地面", "地板"),
    "food": ("food", "beverage", "食品", "饮料", "食物"),
}


def _build_term_map():
    values = {}
    for concept, terms in _CONCEPT_GROUPS.items():
        for term in terms:
            folded = term.casefold()
            if folded not in values:
                values[folded] = []
            if concept not in values[folded]:
                values[folded].append(concept)
    return {term: tuple(concepts) for term, concepts in values.items()}


TERM_CONCEPTS = _build_term_map()
CJK_TERMS = tuple(
    sorted(
        ((term, concepts) for term, concepts in TERM_CONCEPTS.items() if CJK.search(term)),
        key=lambda item: len(item[0]),
        reverse=True,
    )
)
CJK_STOPWORDS = tuple(
    sorted((term for term in STOPWORDS if CJK.search(term)), key=len, reverse=True)
)


def _clip(value, maximum):
    if not isinstance(value, str) or len(value) <= maximum:
        return value
    return value[: maximum - 1] + "…"


def _normalize(text):
    return unicodedata.normalize("NFKC", text or "").casefold()


def _concepts_for_word(word):
    concepts = TERM_CONCEPTS.get(word)
    if concepts is not None:
        return concepts
    if word.endswith("s"):
        concepts = TERM_CONCEPTS.get(word[:-1])
        if concepts is not None:
            return concepts
    if word.endswith("es"):
        concepts = TERM_CONCEPTS.get(word[:-2])
        if concepts is not None:
            return concepts
    return ()


def _token_weights(text, idf=None):
    """Bilingual bag of concepts plus word/character features for the stand-in."""
    normalized = _normalize(text)
    weights = {}

    def add(key, value):
        if idf is not None:
            value *= idf.get(key, 3.0)
        # Sublinear term-frequency saturation keeps repeated aliases/tags from
        # dominating a document's vector.
        weights[key] = min(6.0, weights.get(key, 0.0) + value)

    for word in LATIN.findall(normalized):
        if word in STOPWORDS:
            continue
        concepts = _concepts_for_word(word)
        if concepts:
            for concept in concepts:
                add("c:" + concept, 1.6)
        else:
            add("w:" + word, 0.8)
            if len(word) > 3:
                add("p:" + word[:4], 0.25)
    for run in CJK.findall(normalized):
        if run in STOPWORDS:
            continue
        matched = []
        for term, concepts in CJK_TERMS:
            if term in run:
                for concept in concepts:
                    add("c:" + concept, 1.6)
                matched.append(term)
        residual = run
        for term in sorted(set(matched), key=len, reverse=True):
            residual = residual.replace(term, " ")
        for term in CJK_STOPWORDS:
            residual = residual.replace(term, " ")
        for chunk in residual.split():
            for index in range(len(chunk) - 1):
                add("g:" + chunk[index:index + 2], 0.3)
    return weights


def _normalized_vector(weights, dimension):
    vector = np.zeros(dimension, dtype=np.float32)
    for token, weight in weights.items():
        digest = hashlib.sha256(token.encode("utf-8")).digest()
        index = int.from_bytes(digest[:4], "big") % dimension
        vector[index] += weight if digest[4] & 1 else -weight
    norm = float(np.linalg.norm(vector))
    if norm:
        vector /= norm
    return vector


def _normalize_rows(matrix):
    values = np.asarray(matrix, dtype=np.float32)
    if values.ndim == 1:
        values = values.reshape(1, -1)
    norms = np.linalg.norm(values, axis=1, keepdims=True)
    return values / np.maximum(norms, 1e-12)


def _lexical_coverage(query, text, idf=None):
    query_tokens = _token_weights(query, idf)
    if not query_tokens:
        return 0.0
    document_tokens = _token_weights(text, idf)
    matched = sum(weight for token, weight in query_tokens.items() if token in document_tokens)
    return matched / sum(query_tokens.values())


def _package_version(name):
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return "not-recorded"


@dataclass(frozen=True)
class EmbedderInfo:
    id: str
    version: str
    kind: str
    real: bool
    runtime: str
    dimension: int
    requested: str | None = None
    fallback_reason: str | None = None

    def document(self):
        value = {
            "id": _clip(self.id, 200),
            "version": _clip(self.version, 80),
            "kind": self.kind,
            "real": self.real,
            "runtime": _clip(self.runtime, 120),
            "dimension": self.dimension,
        }
        if self.requested:
            value["requested"] = _clip(self.requested, 200)
        if self.fallback_reason:
            value["fallbackReason"] = _clip(self.fallback_reason, 400)
        return value


class HashEmbedder:
    """Deterministic concept/ngram hashing; no weights and no network."""

    info = EmbedderInfo(
        id=STANDIN_ID,
        version=STANDIN_VERSION,
        kind="deterministic-stand-in",
        real=False,
        runtime="numpy-hash",
        dimension=STANDIN_DIMENSION,
    )

    def __init__(self, *, requested=None, fallback_reason=None):
        self._idf = {}
        self._document_tokens = ()
        if requested or fallback_reason:
            self.info = EmbedderInfo(
                id=STANDIN_ID,
                version=STANDIN_VERSION,
                kind="deterministic-stand-in",
                real=False,
                runtime="numpy-hash",
                dimension=STANDIN_DIMENSION,
                requested=requested,
                fallback_reason=fallback_reason,
            )

    def embed(self, texts, role="document"):
        texts = list(texts)
        if role == "document":
            tokenized = [_token_weights(text) for text in texts]
            document_frequency = {}
            for weights in tokenized:
                for token in weights:
                    document_frequency[token] = document_frequency.get(token, 0) + 1
            total = max(1, len(texts))
            self._idf = {
                token: float(np.log((total + 1) / (count + 1)) + 1)
                for token, count in document_frequency.items()
            }
            tokenized = [_token_weights(text, self._idf) for text in texts]
            # The whole catalog is capped at 10k entries. Cache document tokens
            # for normal-sized snapshots so coverage does not re-tokenize every
            # candidate on every query; larger snapshots fall back to on-demand.
            self._document_tokens = tuple(tokenized) if len(tokenized) <= 5000 else ()
        else:
            tokenized = [_token_weights(text, self._idf or None) for text in texts]
        return np.asarray(
            [_normalized_vector(weights, STANDIN_DIMENSION) for weights in tokenized],
            dtype=np.float32,
        )

    def coverage(self, query, text):
        return _lexical_coverage(query, text, self._idf or None)

    def coverages(self, query, indices):
        if not self._document_tokens:
            return None
        query_tokens = _token_weights(query, self._idf or None)
        total = sum(query_tokens.values())
        if not total:
            return [0.0 for _ in indices]
        values = []
        for index in indices:
            document_tokens = self._document_tokens[index]
            matched = sum(
                weight for token, weight in query_tokens.items() if token in document_tokens
            )
            values.append(matched / total)
        return values


class FastEmbedEmbedder:
    """Optional real CPU multilingual embedder backed by fastembed/ONNX Runtime."""

    def __init__(self, model_name, *, cache_dir=None, threads=None):
        from fastembed import TextEmbedding

        options = {"model_name": model_name}
        if cache_dir:
            options["cache_dir"] = cache_dir
        if threads:
            options["threads"] = int(threads)
        self._model = TextEmbedding(**options)
        lowered = model_name.casefold()
        self._query_prefix = "query: " if "e5" in lowered else ""
        self._document_prefix = "passage: " if "e5" in lowered else ""
        probe = list(self._model.embed(["dimension probe"]))[0]
        self.info = EmbedderInfo(
            id=f"fastembed:{model_name}",
            version=_package_version("fastembed"),
            kind="real-cpu-multilingual",
            real=True,
            runtime="onnxruntime/fastembed",
            dimension=int(np.asarray(probe).shape[-1]),
        )

    def embed(self, texts, role="document"):
        prefix = self._query_prefix if role == "query" else self._document_prefix
        values = list(self._model.embed([prefix + text for text in texts]))
        return _normalize_rows(values)

    def coverage(self, query, text):
        return _lexical_coverage(query, text)


class SentenceTransformerEmbedder:
    """Optional real CPU multilingual embedder backed by sentence-transformers."""

    def __init__(self, model_name, *, cache_dir=None, threads=None):
        module = importlib.import_module("sentence_transformers")
        options = {"device": "cpu"}
        if cache_dir:
            options["cache_folder"] = cache_dir
        self._model = module.SentenceTransformer(model_name, **options)
        self._threads = int(threads) if threads else None
        self.info = EmbedderInfo(
            id=f"sentence-transformers:{model_name}",
            version=_package_version("sentence-transformers"),
            kind="real-cpu-multilingual",
            real=True,
            runtime="torch-cpu",
            dimension=int(self._model.get_sentence_embedding_dimension()),
        )
        if self._threads:
            try:
                import torch

                torch.set_num_threads(self._threads)
            except (ImportError, RuntimeError):
                pass

    def embed(self, texts, role="document"):
        values = self._model.encode(
            list(texts),
            normalize_embeddings=True,
            convert_to_numpy=True,
            show_progress_bar=False,
        )
        return _normalize_rows(values)

    def coverage(self, query, text):
        return _lexical_coverage(query, text)


def load_embedder(spec=None, model=None):
    """Resolve ``LW_CATALOG_EMBEDDER`` or an explicit stand-in/real adapter."""
    value = (spec or os.environ.get("LW_CATALOG_EMBEDDER") or "stand-in").strip()
    model_name = model or os.environ.get("LW_CATALOG_EMBEDDER_MODEL")
    cache_dir = os.environ.get("LW_CATALOG_EMBEDDER_CACHE")
    threads = os.environ.get("LW_CATALOG_EMBEDDER_THREADS")
    kind, _, embedded_model = value.partition(":")
    kind = kind.casefold()
    model_name = model_name or embedded_model or None
    if kind in ("stand-in", "standin", "hash", "deterministic", ""):
        return HashEmbedder()
    if kind == "fastembed":
        if not model_name:
            raise ValueError("fastembed requires LW_CATALOG_EMBEDDER_MODEL or fastembed:<model>")
        return FastEmbedEmbedder(model_name, cache_dir=cache_dir, threads=threads)
    if kind in ("sentence-transformers", "sentence_transformers", "sentence", "st"):
        if not model_name:
            raise ValueError(
                "sentence-transformers requires LW_CATALOG_EMBEDDER_MODEL or "
                "sentence-transformers:<model>"
            )
        return SentenceTransformerEmbedder(model_name, cache_dir=cache_dir, threads=threads)
    raise ValueError(f"Unknown semantic embedder: {value}")


def _entry_text(entry):
    names = getattr(entry, "names", {}) or {}
    aliases = getattr(entry, "aliases", {}) or {}
    descriptions = getattr(entry, "descriptions", {}) or {}
    tags = getattr(entry, "tags", {}) or {}
    parts = [
        names.get("en", ""), names.get("zh", ""),
        names.get("en", ""), names.get("zh", ""),
    ]
    for language in ("en", "zh"):
        parts.extend(_clip(value, 160) for value in list(aliases.get(language, []))[:12])
    for language in ("en", "zh"):
        parts.extend(_clip(value, 100) for value in list(tags.get(language, []))[:12])
    for language in ("en", "zh"):
        parts.append(_clip(descriptions.get(language, ""), 900))
    parts.extend((
        getattr(entry, "kind", "") or "",
        getattr(entry, "package_id", "") or "",
        getattr(entry, "local_asset_id", "") or "",
    ))
    usage = getattr(entry, "usage", {}) or {}
    for name, label in (
        ("dressing", "dressing 布景"),
        ("staticCollision", "static collision 静态碰撞"),
    ):
        fact = usage.get(name)
        eligible = getattr(fact, "eligible", None)
        if eligible is True:
            parts.append(label)
        elif eligible is False:
            parts.append(label + " unavailable 不可用")
    text = " ".join(part for part in parts if part)
    return _clip(text, MAX_DOCUMENT_CHARS)


@dataclass(frozen=True)
class SemanticHit:
    entry: object
    score: float
    coverage: float | None


class SemanticIndex:
    """One immutable semantic index over one catalog snapshot and one embedder."""

    def __init__(self, entries, snapshot_id, embedder, *, threshold=None,
                 min_coverage=None):
        self.entries = tuple(entries)
        self.snapshot_id = snapshot_id
        self.embedder = embedder
        self.documents = tuple(_entry_text(entry) for entry in self.entries)
        if self.documents:
            vectors = embedder.embed(self.documents, role="document")
        else:
            vectors = np.zeros((0, max(1, embedder.info.dimension)), dtype=np.float32)
        self.vectors = _normalize_rows(vectors)
        self.threshold = (
            float(threshold) if threshold is not None
            else (REAL_THRESHOLD if embedder.info.real else STANDIN_THRESHOLD)
        )
        self.min_coverage = (
            float(min_coverage) if min_coverage is not None
            else STANDIN_MIN_COVERAGE
        )
        self.coverage_bypass = REAL_COVERAGE_BYPASS if embedder.info.real else None
        self.version = self._version()

    def _version(self):
        digest = hashlib.sha256()
        digest.update(SEMANTIC_SCHEMA.encode("utf-8"))
        digest.update((self.snapshot_id or "").encode("utf-8"))
        info = self.embedder.info
        digest.update("|".join((info.id, info.version, info.kind, str(info.dimension))).encode())
        digest.update(self.vectors.astype(np.float32).tobytes())
        for entry in self.entries:
            digest.update(getattr(entry, "id", "").encode("utf-8"))
            digest.update(b"\n")
        return "sha256:" + digest.hexdigest()

    @property
    def notice(self):
        info = self.embedder.info
        if info.real:
            return (
                "Real CPU multilingual embedder; semantic scores are soft ranking only. "
                "Quality and Linux/WSL performance evidence are separate."
            )
        if info.fallback_reason:
            return (
                "The requested real embedder was unavailable, so the deterministic CI stand-in "
                "was used. This is not real-model evidence."
            )
        return (
            "Deterministic CI stand-in; it is not a real multilingual model and its scores are "
            "not model-quality evidence."
        )

    def document(self):
        info = self.embedder.info
        return {
            "schema": SEMANTIC_SCHEMA,
            "enabled": True,
            "model": info.document(),
            "index": {
                "version": self.version,
                "entryCount": len(self.entries),
            },
            "threshold": round(self.threshold, 6),
            "minCoverage": (
                round(self.min_coverage, 6) if self.min_coverage is not None else None
            ),
            "coverageBypass": (
                round(self.coverage_bypass, 6) if self.coverage_bypass is not None else None
            ),
            "notice": self.notice,
        }

    def search(self, query, limit=50):
        if not self.documents:
            return []
        query_vector = _normalize_rows(self.embedder.embed([query], role="query"))[0]
        scores = self.vectors @ query_vector
        order = np.argsort(-scores, kind="stable")
        candidates = [
            int(index) for index in order[: max(1, int(limit))]
            if float(scores[index]) >= self.threshold
        ]
        coverages = None
        if self.min_coverage is not None:
            if hasattr(self.embedder, "coverages"):
                coverages = self.embedder.coverages(query, candidates)
            if coverages is None:
                coverages = [
                    self.embedder.coverage(query, self.documents[index])
                    for index in candidates
                ]
        hits = []
        for position, index in enumerate(candidates):
            score = float(scores[index])
            coverage = None if coverages is None else float(coverages[position])
            if coverage is not None and coverage < self.min_coverage:
                if self.coverage_bypass is None or score < self.coverage_bypass:
                    continue
            hits.append(SemanticHit(self.entries[index], score, coverage))
        return hits


def build_semantic_index(entries, snapshot_id, *, embedder=None, spec=None, model=None,
                         threshold=None, min_coverage=None, allow_fallback=True):
    """Build the index, falling back to the labelled stand-in rather than failing."""
    requested = spec or os.environ.get("LW_CATALOG_EMBEDDER") or (
        getattr(getattr(embedder, "info", None), "id", None) or "stand-in"
    )
    try:
        selected = embedder if embedder is not None else load_embedder(spec, model)
        return SemanticIndex(
            entries, snapshot_id, selected, threshold=threshold, min_coverage=min_coverage
        )
    except Exception as exc:  # optional model imports, downloads, runtime failures
        if not allow_fallback:
            raise
        LOGGER.warning("Semantic embedder unavailable; using deterministic stand-in: %s", exc)
        fallback = HashEmbedder(requested=str(requested), fallback_reason=str(exc))
        return SemanticIndex(
            entries,
            snapshot_id,
            fallback,
            threshold=STANDIN_THRESHOLD,
            min_coverage=STANDIN_MIN_COVERAGE,
        )
