import pandas as pd
import ta
import numpy as np # Import numpy for NaN handling

class TechnicalIndicators:
    def __init__(self, logger):
        self.logger = logger

    def _check_dataframe(self, df: pd.DataFrame, required_columns: list[str], indicator_name: str) -> bool:
        """Helper to check DataFrame validity and columns."""
        if df.empty:
            self.logger.warning(f"DataFrame is empty for {indicator_name} calculation. Returning empty.")
            return False
        if not all(col in df.columns for col in required_columns):
            self.logger.warning(f"Missing required columns {required_columns} for {indicator_name} calculation. Returning empty.")
            return False
        return True

    def calculate_ema(self, df: pd.DataFrame, length: int) -> pd.Series:
        """Calculate Exponential Moving Average (EMA)"""
        indicator_name = f"EMA({length})"
        if not self._check_dataframe(df, ["Close"], indicator_name):
            return pd.Series(dtype='float64')
        if len(df) < length:
            self.logger.warning(f"Insufficient data for {indicator_name} (need {length}, got {len(df)}).")
            return pd.Series(dtype='float64')
        try:
            ema = ta.trend.ema_indicator(df["Close"], window=length)
            self.logger.debug(f"{indicator_name} calculated.")
            return ema.bfill() # Fill NaN values
        except Exception as e:
            self.logger.error(f"Error calculating {indicator_name}: {e}")
            return pd.Series(dtype='float64')

    def calculate_rsi(self, df: pd.DataFrame, length: int) -> pd.Series:
        """Calculate Relative Strength Index (RSI)"""
        indicator_name = f"RSI({length})"
        if not self._check_dataframe(df, ["Close"], indicator_name):
            return pd.Series(dtype='float64')
        if len(df) < length:
            self.logger.warning(f"Insufficient data for {indicator_name} (need {length}, got {len(df)}).")
            return pd.Series(dtype='float64')
        try:
            rsi = ta.momentum.rsi(df["Close"], window=length)
            self.logger.debug(f"{indicator_name} calculated.")
            return rsi.bfill() # Fill NaN values
        except Exception as e:
            self.logger.error(f"Error calculating {indicator_name}: {e}")
            return pd.Series(dtype='float64')

    def calculate_macd(self, df: pd.DataFrame, fast: int, slow: int, signal: int) -> pd.DataFrame:
        """Calculate Moving Average Convergence Divergence (MACD)"""
        indicator_name = f"MACD({fast},{slow},{signal})"
        if not self._check_dataframe(df, ["Close"], indicator_name):
            return pd.DataFrame()
        if len(df) < slow or len(df) < fast or len(df) < signal:
            self.logger.warning(f"Insufficient data for {indicator_name} (need max({fast},{slow},{signal}), got {len(df)}).")
            return pd.DataFrame()
        try:
            macd_line = ta.trend.macd(df["Close"], window_slow=slow, window_fast=fast)
            macd_signal = ta.trend.macd_signal(df["Close"], window_slow=slow, window_fast=fast, window_sign=signal)
            macd_hist = ta.trend.macd_diff(df["Close"], window_slow=slow, window_fast=fast, window_sign=signal)
            
            macd_df = pd.DataFrame({
                'MACD_Line': macd_line,
                'MACD_Signal': macd_signal,
                'MACD_Hist': macd_hist
            })
            self.logger.debug(f"{indicator_name} calculated.")
            return macd_df.bfill() # Fill NaN values
        except Exception as e:
            self.logger.error(f"Error calculating {indicator_name}: {e}")
            return pd.DataFrame()

    def calculate_all_indicators(self, df: pd.DataFrame, indicators_config) -> pd.DataFrame:
        """Calculates all configured technical indicators for a given DataFrame."""
        # EMA 50
        df[f'EMA_{indicators_config.ema_50}'] = self.calculate_ema(df, indicators_config.ema_50)
        # EMA 200
        df[f'EMA_{indicators_config.ema_200}'] = self.calculate_ema(df, indicators_config.ema_200)
        # RSI
        df['RSI'] = self.calculate_rsi(df, indicators_config.rsi_length)
        # MACD
        macd_data = self.calculate_macd(df, indicators_config.macd_fast, indicators_config.macd_slow, indicators_config.macd_signal)
        df = df.join(macd_data)
        # ATR
        df['ATR'] = self.calculate_atr(df, indicators_config.atr_length)
        # Bollinger Bands
        bb_data = self.calculate_bollinger_bands(df, indicators_config.bollinger_bands_length, indicators_config.bollinger_bands_std)
        df = df.join(bb_data)
        
        return df

    def calculate_atr(self, df: pd.DataFrame, length: int) -> pd.Series:
        """Calculate Average True Range (ATR)"""
        indicator_name = f"ATR({length})"
        if not self._check_dataframe(df, ["High", "Low", "Close"], indicator_name):
            return pd.Series(dtype='float64')
        if len(df) < length:
            self.logger.warning(f"Insufficient data for {indicator_name} (need {length}, got {len(df)}).")
            return pd.Series(dtype='float64')
        try:
            atr = ta.volatility.average_true_range(df["High"], df["Low"], df["Close"], window=length)
            self.logger.debug(f"{indicator_name} calculated.")
            return atr.bfill() # Fill NaN values
        except Exception as e:
            self.logger.error(f"Error calculating {indicator_name}: {e}")
            return pd.Series(dtype='float64')

    def calculate_bollinger_bands(self, df: pd.DataFrame, length: int, std: float) -> pd.DataFrame:
        """Calculate Bollinger Bands (BBANDS)"""
        indicator_name = f"BollingerBands({length},{std})"
        if not self._check_dataframe(df, ["Close"], indicator_name):
            return pd.DataFrame()
        if len(df) < length:
            self.logger.warning(f"Insufficient data for {indicator_name} (need {length}, got {len(df)}).")
            return pd.DataFrame()
        try:
            bollinger = ta.volatility.BollingerBands(df["Close"], window=length, window_dev=std)
            bb_df = pd.DataFrame({
                'BB_Lower': bollinger.bollinger_lband(),
                'BB_Mid': bollinger.bollinger_mavg(),
                'BB_Upper': bollinger.bollinger_hband()
            })
            self.logger.debug(f"{indicator_name} calculated.")
            return bb_df.bfill() # Fill NaN values
        except Exception as e:
            self.logger.error(f"Error calculating {indicator_name}: {e}")
            return pd.DataFrame()