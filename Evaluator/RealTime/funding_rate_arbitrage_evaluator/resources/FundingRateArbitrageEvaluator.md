# Funding Rate Arbitrage Evaluator

## Overview

The Funding Rate Arbitrage Evaluator monitors funding rates across different cryptocurrency exchanges to identify arbitrage opportunities. This evaluator helps traders profit from funding rate differences by suggesting positions that capture the rate spreads while remaining market neutral.

## How It Works

### Funding Rate Arbitrage Strategy

Funding rates are periodic payments between long and short position holders in perpetual futures contracts. When funding rates differ significantly between exchanges, there's an opportunity to:

1. **Long position on exchange with negative/lower funding rate** (receive funding or pay less)
2. **Short position on exchange with positive/higher funding rate** (pay less or receive funding)
3. **Profit from the rate differential** while being market neutral

### Detection Algorithm

The evaluator:

1. **Monitors funding rates** across all connected exchanges for each symbol
2. **Compares rates pairwise** between exchanges
3. **Identifies opportunities** when rate differences exceed the configured threshold
4. **Calculates confidence scores** based on rate spreads and absolute values
5. **Generates evaluation signals** proportional to profit potential

## Configuration Parameters

### Rate Difference Threshold (%)
- **Default:** 1.0%
- **Range:** 0.1% - 10.0%
- **Description:** Minimum funding rate difference between exchanges to trigger an arbitrage signal. Higher values reduce false signals but may miss smaller opportunities.

### Minimum Absolute Rate (%)
- **Default:** 0.5%
- **Range:** 0.01% - 5.0%
- **Description:** Minimum absolute funding rate to consider for arbitrage. Ensures that at least one exchange has a meaningful funding rate.

### Max Exchanges to Compare
- **Default:** 5
- **Range:** 2 - 10
- **Description:** Maximum number of exchanges to compare for arbitrage opportunities. Limits computation while focusing on the best opportunities.

### Evaluation Interval (minutes)
- **Default:** 1 minute
- **Range:** 1 - 60 minutes
- **Description:** How often to check for arbitrage opportunities. More frequent checks provide faster signals but increase computational load.

### Minimum Liquidity Threshold (USD)
- **Default:** 100,000 USD
- **Range:** 1,000 - 10,000,000 USD
- **Description:** Minimum liquidity required to consider an arbitrage opportunity viable. Ensures sufficient market depth for execution.

## Evaluation Scoring

The evaluator generates scores from 0 to 1 based on:

- **Rate Difference:** Higher spreads = higher scores
- **Confidence:** Based on absolute rates and spread magnitude
- **Multiple Opportunities:** Additional opportunities increase the score

### Score Interpretation

- **0.0 - 0.3:** Weak arbitrage opportunity
- **0.3 - 0.6:** Moderate arbitrage opportunity  
- **0.6 - 1.0:** Strong arbitrage opportunity

## Risk Considerations

### Market Risks
- **Basis Risk:** Price differences between exchanges during position entry/exit
- **Execution Risk:** Slippage and timing differences between exchanges
- **Liquidity Risk:** Insufficient depth for large positions

### Operational Risks
- **Exchange Risk:** Downtime, connectivity issues, or technical problems
- **Margin Requirements:** Different margin requirements across exchanges
- **Funding Schedule:** Different funding times and calculation methods

### Mitigation Strategies
- Monitor multiple exchanges simultaneously
- Use conservative position sizing
- Implement proper risk management rules
- Account for transaction costs and fees

## Usage Tips

1. **Multi-Exchange Setup:** Ensure you have accounts on multiple exchanges that support the same symbols
2. **Real-time Data:** Use exchanges with real-time funding rate updates
3. **Position Management:** Implement automated position management to capture opportunities quickly
4. **Fee Awareness:** Account for trading fees in your arbitrage calculations
5. **Capital Allocation:** Reserve sufficient capital for margin requirements on both exchanges

## Technical Implementation

The evaluator:
- Inherits from `RealTimeEvaluator` for continuous monitoring
- Uses async methods for efficient multi-exchange data fetching
- Implements caching to avoid redundant API calls
- Provides external access to opportunity data for strategy integration

## Example Opportunity

```
Symbol: BTC/USDT
Exchange A Funding Rate: -0.0050 (negative, receive funding)
Exchange B Funding Rate: +0.0150 (positive, pay funding)
Rate Difference: 0.0200 (2.0%)
Action: Long on Exchange A, Short on Exchange B
Expected Profit: ~2.0% over funding period
```

This opportunity would generate a high evaluation score due to the significant rate difference and potential profit.