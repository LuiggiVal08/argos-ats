"""Application ports (Protocol classes for adapter contracts).

Each port abstracts an I/O concern that the use cases need but must
not couple to. The infrastructure layer provides the concrete
adapters (CCXT, TA, broker, Keras).
"""
from .atr_calculator import AtrCalculator, AtrCalculatorError
from .backtest_reporter import BacktestReporter, MetricsCalculator
from .balance_provider import BalanceProvider, BalanceProviderError
from .checkpoint_repository import (
    CheckpointIOError,
    CheckpointNotFoundError,
    CheckpointRepository,
)
from .class_balancer import ClassBalancer
from .confidence_filter import ConfidenceFilter
from .data_preprocessor import (
    DataPreprocessor,
    InsufficientDataError,
    PreprocessingError,
)
from .exchange_order_gateway import (
    ExchangeOrderGateway,
    ExchangeOrderGatewayError,
)
from .execution_logger import ExecutionLogger
from .feature_analyzer import AnalysisError, FeatureAnalyzer
from .feature_store import FeatureStore
from .incident_reporter import IncidentReporter
from .notifier import Notifier
from .incident_repository import IncidentRepository
from .meta_model import MetaModel
from .min_lot_provider import MarketConstraints, MinLotProvider, MinLotProviderError
from .model_predictor import ModelPredictor, PredictionError
from .model_trainer import ModelTrainer, TrainingError
from .multi_symbol_consolidator import MultiSymbolConsolidator
from .ohlcv_source import OhlcvSource, OhlcvSourceError
from .position_repository import PositionRepository
from .probability_calibrator import ProbabilityCalibrator
from .regime_detector import RegimeDetector, RegimeDetectionError
from .signal_consumer import SignalConsumer
from .strategy import Strategy, StrategyRegistry
from .uncertainty_estimator import UncertaintyEstimator
from .validation_reporter import ValidationReporter

__all__ = [
    "AnalysisError",
    "AtrCalculator",
    "ExchangeOrderGateway",
    "ExchangeOrderGatewayError",
    "AtrCalculatorError",
    "BacktestReporter",
    "BalanceProvider",
    "BalanceProviderError",
    "CheckpointIOError",
    "CheckpointNotFoundError",
    "CheckpointRepository",
    "ClassBalancer",
    "ConfidenceFilter",
    "DataPreprocessor",
    "ExecutionLogger",
    "FeatureAnalyzer",
    "FeatureStore",
    "IncidentReporter",
    "Notifier",
    "IncidentRepository",
    "InsufficientDataError",
    "MarketConstraints",
    "MetaModel",
    "MetricsCalculator",
    "MinLotProvider",
    "MinLotProviderError",
    "ModelPredictor",
    "ModelTrainer",
    "MultiSymbolConsolidator",
    "OhlcvSource",
    "OhlcvSourceError",
    "PositionRepository",
    "PredictionError",
    "PreprocessingError",
    "ProbabilityCalibrator",
    "RegimeDetector",
    "RegimeDetectionError",
    "SignalConsumer",
    "Strategy",
    "StrategyRegistry",
    "TrainingError",
    "UncertaintyEstimator",
    "ValidationReporter",
]
