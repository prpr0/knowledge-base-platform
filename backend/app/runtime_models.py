"""从已授权本地资源加载真实生成tokenizer、嵌入与重排模型。"""
from functools import lru_cache
from threading import Lock
import math
from .settings import Settings

class RuntimeModels:
    """延迟加载模型并避免并发首次请求重复占用内存。"""
    def __init__(self, settings: Settings):
        """保存本地路径，构造时不下载或加载权重。"""
        self.settings = settings
        self._lock = Lock()
        self._tokenizer = None
        self._embedding = None
        self._reranker = None

    @property
    def tokenizer(self):
        """只使用本地Qwen tokenizer，缺失时明确失败。"""
        with self._lock:
            if self._tokenizer is None:
                from transformers import AutoTokenizer
                self._tokenizer = AutoTokenizer.from_pretrained(str(self.settings.tokenizer_path), local_files_only=True, trust_remote_code=False)
        return self._tokenizer

    def embed(self, texts: list[str]) -> list[list[float]]:
        """生成归一化的真实1024维BGE-M3向量，不接受伪向量。"""
        with self._lock:
            if self._embedding is None:
                import torch
                from sentence_transformers import SentenceTransformer
                torch.set_num_threads(4)
                self._embedding = SentenceTransformer(str(self.settings.embedding_model_path), device='cpu', local_files_only=True, trust_remote_code=False)
            vectors = self._embedding.encode(texts, batch_size=4, normalize_embeddings=True, convert_to_numpy=True, show_progress_bar=False)
        result = vectors.tolist()
        if len(result) != len(texts) or any(len(vector) != 1024 or not all(math.isfinite(value) for value in vector) for vector in result):
            raise RuntimeError('嵌入模型未返回有效的1024维向量')
        return result

    def rerank(self, query: str, texts: list[str]) -> list[float]:
        """仅对后端已经放行的片段执行真实BGE重排。"""
        with self._lock:
            if self._reranker is None:
                from sentence_transformers import CrossEncoder
                self._reranker = CrossEncoder(str(self.settings.reranker_model_path), device='cpu', local_files_only=True, trust_remote_code=False)
            values = self._reranker.predict([(query, text) for text in texts], batch_size=4, show_progress_bar=False)
        result = [float(value) for value in values]
        if len(result) != len(texts) or not all(math.isfinite(value) for value in result):
            raise RuntimeError('重排模型未返回有效分数')
        return result

@lru_cache(maxsize=1)
def runtime_models(settings: Settings) -> RuntimeModels:
    """每个进程复用同一配置的模型实例，不进行网络回退。"""
    return RuntimeModels(settings)
