"use client";

import { useEffect, useState, useCallback } from "react";
import useUser from "@/lib/authClient";
import AIDostModal from "../components/AIDostModal";
import AIReportModal from "../components/AIReportModal";
import Navbar from "../components/Navbar";

export default function PortfolioPage() {
  const { user, isSignedIn, isLoading } = useUser();
  const [portfolioItems, setPortfolioItems] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [showAIDost, setShowAIDost] = useState(false);
  const [showAIReport, setShowAIReport] = useState(false);
  const [riskVolatility, setRiskVolatility] = useState({});
  const [monteCarlo, setMonteCarlo] = useState({});

  // Buy history modal state
  const [showHistoryModal, setShowHistoryModal] = useState(false);
  const [selectedSymbol, setSelectedSymbol] = useState(null);
  const [historyData, setHistoryData] = useState([]);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [selectedItem, setSelectedItem] = useState(null);

  // Function to calculate aggregate portfolio metrics
  const calculatePortfolioMetrics = useCallback(async () => {
    if (!portfolioItems.length) return;

    try {
      const totalRisk = portfolioItems.reduce(
        (acc, item) => acc + (item.risk_volatility?.annualized_volatility || 0),
        0,
      );
      const avgVolatility = totalRisk / portfolioItems.length;

      const totalReturn = portfolioItems.reduce(
        (acc, item) => acc + (item.risk_volatility?.annualized_return || 0),
        0,
      );
      const avgReturn = totalReturn / portfolioItems.length;

      setRiskVolatility({
        annualized_volatility: avgVolatility,
        annualized_return: avgReturn,
        sharpe_ratio: (avgReturn - 0.05) / avgVolatility,
      });

      setMonteCarlo({
        expected_nav: portfolioItems.reduce(
          (acc, item) => acc + (item.nav || 0),
          0,
        ),
        probability_positive_return: avgReturn > 0 ? 75 : 45,
        lower_bound_5th_percentile: avgReturn * 0.95,
        upper_bound_95th_percentile: avgReturn * 1.05,
      });
    } catch (error) {
      console.error("Error calculating portfolio metrics:", error);
    }
  }, [portfolioItems]);

  useEffect(() => {
    calculatePortfolioMetrics();
  }, [calculatePortfolioMetrics]);

  useEffect(() => {
    const fetchPortfolio = async () => {
      if (isLoading) {
        return; // Still loading, don't fetch yet
      }

      if (!isSignedIn || !user) {
        setError("Please sign in to view your portfolio");
        setLoading(false);
        return;
      }

      try {
        // Fetch analytics which includes all holdings with computed metrics
        const response = await fetch("/api/backend/analytics/portfolio", {
          cache: "no-store",
        });

        if (!response.ok) {
          throw new Error("Failed to fetch portfolio analytics");
        }

        const analytics = await response.json();

        // Map backend response structure to our state
        // Backend returns: { summary: {...}, holdings: [...] }
        // Each holding includes: id, symbol, name, asset_type, quantity, buy_price, current_price, pnl, xirr, montecarlo, risk
        const itemsWithMetrics = analytics.holdings.map((holding) => ({
          // Map to the old item_type field name for compatibility with UI
          id: holding.id,
          symbol: holding.symbol,
          name: holding.name,
          item_type:
            holding.asset_type === "mutualfund"
              ? "mutual_fund"
              : holding.asset_type,
          quantity: holding.quantity,
          buy_price: holding.buy_price,
          current_price: holding.current_price,
          current_value: holding.current_value,
          invested: holding.invested,
          // Add computed metrics in risk_volatility structure for portfolio metric calculations
          risk_volatility: holding.risk || {},
          // Include monte carlo data
          montecarlo: holding.monte_carlo || {},
          // Store raw holding data for modal display
          _backendHolding: holding,
        }));

        setPortfolioItems(itemsWithMetrics);

        // Update portfolio metrics from backend summary
        if (analytics.summary) {
          setRiskVolatility(analytics.summary || {});
          setMonteCarlo(analytics.summary?.montecarlo || {});
        }
      } catch (err) {
        console.error("Error fetching portfolio:", err);
        setError("Failed to fetch your portfolio. Please try again.");
      } finally {
        setLoading(false);
      }
    };

    fetchPortfolio();
  }, [isSignedIn, user, isLoading]);

  const handleRemoveItem = async (itemId) => {
    if (!isSignedIn || !user) {
      alert("Please sign in to remove items");
      return;
    }

    try {
      const response = await fetch(`/api/backend/portfolio/holding/${itemId}`, {
        method: "DELETE",
      });

      if (!response.ok) {
        throw new Error("Failed to remove item");
      }

      setPortfolioItems((items) => items.filter((item) => item.id !== itemId));
      alert("Item removed successfully!");

      // Refetch analytics to ensure UI is in sync with backend
      const analyticsResponse = await fetch(
        "/api/backend/analytics/portfolio",
        {
          cache: "no-store",
        },
      );

      if (analyticsResponse.ok) {
        const analytics = await analyticsResponse.json();
        const itemsWithMetrics = analytics.holdings.map((holding) => ({
          id: holding.id,
          symbol: holding.symbol,
          name: holding.name,
          item_type:
            holding.asset_type === "mutualfund"
              ? "mutual_fund"
              : holding.asset_type,
          quantity: holding.quantity,
          buy_price: holding.buy_price,
          current_price: holding.current_price,
          current_value: holding.current_value,
          invested: holding.invested,
          risk_volatility: holding.risk || {},
          montecarlo: holding.monte_carlo || {},
          _backendHolding: holding,
        }));
        setPortfolioItems(itemsWithMetrics);
      }
    } catch (err) {
      console.error("Error removing item:", err);
      alert("Failed to remove item. Please try again.");
    }
  };

  const handleViewHistory = async (symbol, name) => {
    if (!isSignedIn || !user) {
      alert("Please sign in to view holding history");
      return;
    }

    try {
      setHistoryLoading(true);
      setSelectedSymbol({ symbol, name });

      // Find the portfolio item to get current_price
      const item = portfolioItems.find((p) => p.symbol === symbol);
      setSelectedItem(item);

      const response = await fetch(
        `/api/backend/portfolio/history/${encodeURIComponent(symbol)}`,
      );

      if (!response.ok) {
        throw new Error("Failed to fetch holding history");
      }

      const data = await response.json();
      setHistoryData(data);
      setShowHistoryModal(true);
    } catch (err) {
      console.error("Error fetching history:", err);
      alert("Failed to load buy history. Please try again.");
    } finally {
      setHistoryLoading(false);
    }
  };

  const handleDeleteLot = async (lotId) => {
    if (!window.confirm("Are you sure you want to delete this lot?")) {
      return;
    }

    try {
      const response = await fetch(`/api/backend/portfolio/holding/${lotId}`, {
        method: "DELETE",
      });

      if (!response.ok) {
        throw new Error("Failed to delete lot");
      }

      // Remove from history
      setHistoryData((prev) => prev.filter((lot) => lot.id !== lotId));

      // Refetch portfolio items to update the aggregated card
      const analyticsResponse = await fetch(
        "/api/backend/analytics/portfolio",
        {
          cache: "no-store",
        },
      );

      if (analyticsResponse.ok) {
        const analytics = await analyticsResponse.json();
        const itemsWithMetrics = analytics.holdings.map((holding) => ({
          id: holding.id,
          symbol: holding.symbol,
          name: holding.name,
          item_type:
            holding.asset_type === "mutualfund"
              ? "mutual_fund"
              : holding.asset_type,
          quantity: holding.quantity,
          buy_price: holding.buy_price,
          current_price: holding.current_price,
          current_value: holding.current_value,
          invested: holding.invested,
          risk_volatility: holding.risk || {},
          montecarlo: holding.monte_carlo || {},
          _backendHolding: holding,
        }));
        setPortfolioItems(itemsWithMetrics);
      }

      alert("Lot deleted successfully!");
    } catch (err) {
      console.error("Error deleting lot:", err);
      alert("Failed to delete lot. Please try again.");
    }
  };

  if (!isSignedIn && !isLoading) {
    return (
      <div className="min-h-screen py-12 px-4 bg-linear-to-b from-[#050511] via-[#0d1020] to-[#0b0b12]">
        <Navbar />
        <div className="max-w-7xl mx-auto text-center">
          <h1 className="text-3xl font-bold text-white mb-4">Please Sign In</h1>
          <p className="text-gray-400">
            You need to be signed in to view your portfolio.
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen py-12 px-4 bg-linear-to-b from-[#050511] via-[#0d1020] to-[#0b0b12]">
      <Navbar /> {/* <-- Added Navbar here */}
      <div className="max-w-7xl mt-12">
        <div className="mb-8">
          <div className="flex flex-col md:flex-row md:justify-between md:items-center gap-4">
            <div>
              <h1 className="text-3xl font-bold text-white mb-2">
                My Portfolio
              </h1>
              <p className="text-gray-400">Manage your finance</p>
            </div>

            <div className="flex gap-4">
              <button
                onClick={() => setShowAIDost(true)}
                className="bg-linear-to-r from-cyan-500 to-blue-600 hover:from-cyan-600 hover:to-blue-700 text-white px-6 py-2.5 rounded-lg font-semibold text-base transition-all transform hover:scale-105 shadow-lg flex items-center gap-2"
              >
                <svg
                  className="w-5 h-5"
                  fill="none"
                  stroke="currentColor"
                  viewBox="0 0 24 24"
                >
                  <path
                    strokeLinecap="round"
                    strokeLinejoin="round"
                    strokeWidth={2}
                    d="M8 10h.01M12 10h.01M16 10h.01M9 16H5a2 2 0 01-2-2V6a2 2 0 012-2h14a2 2 0 012 2v8a2 2 0 01-2 2h-5l-5 5v-5z"
                  />
                </svg>
                AI Dost
              </button>
              <button
                onClick={() => setShowAIReport(true)}
                className="bg-linear-to-r from-purple-600 to-pink-600 hover:from-purple-700 hover:to-pink-700 text-white px-6 py-2.5 rounded-lg font-semibold text-base transition-all transform hover:scale-105 shadow-lg flex items-center gap-2"
              >
                <svg
                  className="w-5 h-5"
                  fill="none"
                  stroke="currentColor"
                  viewBox="0 0 24 24"
                >
                  <path
                    strokeLinecap="round"
                    strokeLinejoin="round"
                    strokeWidth={2}
                    d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z"
                  />
                </svg>
                AI Report
              </button>
            </div>
          </div>
        </div>

        {loading ? (
          <div className="flex justify-center items-center min-h-[200px]">
            <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-white"></div>
          </div>
        ) : error ? (
          <div className="bg-red-500/10 border border-red-500/20 rounded-lg p-4 text-red-400">
            {error}
          </div>
        ) : portfolioItems.length === 0 ? (
          <div className="text-center py-12 bg-white/5 rounded-lg">
            <p className="text-gray-400">
              Your portfolio is empty. Add some stocks or mutual funds from the
              dashboards!
            </p>
          </div>
        ) : (
          <div className="grid gap-6 md:grid-cols-2 lg:grid-cols-3">
            {portfolioItems.map((item) => (
              <div
                key={item.id}
                onClick={() => handleViewHistory(item.symbol, item.name)}
                className="bg-white/5 rounded-lg p-6 border border-white/10 cursor-pointer hover:bg-white/10 hover:border-white/20 transition-all duration-200"
              >
                <div className="flex justify-between items-start mb-4">
                  <div>
                    <h3 className="text-lg font-semibold text-white mb-1">
                      {item.name}
                    </h3>
                    <p className="text-sm text-gray-400">{item.symbol}</p>
                  </div>
                  <span
                    className="px-3 py-1 rounded-full text-xs font-medium capitalize"
                    style={{
                      backgroundColor:
                        item.item_type === "stock"
                          ? "rgba(16, 185, 129, 0.1)"
                          : "rgba(139, 92, 246, 0.1)",
                      color: item.item_type === "stock" ? "#10B981" : "#8B5CF6",
                    }}
                  >
                    {item.item_type}
                  </span>
                </div>

                {/* Investment Details */}
                <div className="space-y-3 mb-4 text-sm">
                  <div className="flex justify-between">
                    <span className="text-gray-400">Quantity:</span>
                    <span className="text-white font-medium">
                      {Number(item.quantity).toLocaleString("en-IN", {
                        maximumFractionDigits: 4,
                      })}
                    </span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-gray-400">Buy Price:</span>
                    <span className="text-white font-medium">
                      ₹
                      {Number(item.buy_price ?? 0).toLocaleString("en-IN", {
                        maximumFractionDigits: 2,
                      })}
                    </span>
                  </div>
                  <div className="flex justify-between bg-white/10 rounded px-3 py-2">
                    <span className="text-gray-300 font-medium">Invested:</span>
                    <span className="text-green-400 font-bold">
                      ₹
                      {(
                        Number(item.quantity ?? 0) * Number(item.buy_price ?? 0)
                      ).toLocaleString("en-IN", { maximumFractionDigits: 2 })}
                    </span>
                  </div>
                  {item.current_price != null && item.current_price > 0 && (
                    <>
                      <div className="flex justify-between">
                        <span className="text-gray-400">Current Price:</span>
                        <span className="text-white font-medium">
                          ₹
                          {Number(item.current_price).toLocaleString("en-IN", {
                            maximumFractionDigits: 2,
                          })}
                        </span>
                      </div>
                      <div className="flex justify-between">
                        <span className="text-gray-400">Current Value:</span>
                        <span className="text-white font-medium">
                          ₹
                          {Number(item.current_value || 0).toLocaleString(
                            "en-IN",
                            {
                              maximumFractionDigits: 2,
                            },
                          )}
                        </span>
                      </div>
                      <div className="flex justify-between bg-white/10 rounded px-3 py-2">
                        <span className="text-gray-300 font-medium">P&L:</span>
                        <div className="text-right">
                          <div
                            className={`font-bold text-lg ${(item._backendHolding?.pnl ?? 0) >= 0 ? "text-green-400" : "text-red-400"}`}
                          >
                            {(item._backendHolding?.pnl ?? 0) >= 0 ? "+" : ""}₹
                            {Number(
                              item._backendHolding?.pnl ?? 0,
                            ).toLocaleString("en-IN", {
                              maximumFractionDigits: 2,
                            })}
                          </div>
                          <div
                            className={`text-sm font-semibold ${(item._backendHolding?.pnl_pct ?? 0) >= 0 ? "text-green-300" : "text-red-300"}`}
                          >
                            {(item._backendHolding?.pnl_pct ?? 0) >= 0
                              ? "+"
                              : ""}
                            {Number(item._backendHolding?.pnl_pct ?? 0).toFixed(
                              2,
                            )}
                            %
                          </div>
                        </div>
                      </div>
                    </>
                  )}
                </div>

                <div className="flex justify-between items-center text-xs text-gray-500 border-t border-white/10 pt-3">
                  <span>
                    Added:{" "}
                    {item._backendHolding?.created_at
                      ? new Date(
                          item._backendHolding.created_at,
                        ).toLocaleDateString()
                      : "—"}
                  </span>
                  <button
                    onClick={() => handleRemoveItem(item.id)}
                    className="text-red-400 hover:text-red-300 transition-colors font-medium"
                  >
                    Remove
                  </button>
                </div>
              </div>
            ))}
          </div>
        )}

        {/* AI Modals */}
        <AIDostModal
          isOpen={showAIDost}
          onClose={() => setShowAIDost(false)}
          fundData={{
            meta: {
              portfolio_size: portfolioItems.length,
              stocks_count: portfolioItems.filter(
                (i) => i.item_type === "stock",
              ).length,
              mutual_funds_count: portfolioItems.filter(
                (i) => i.item_type === "mutual_fund",
              ).length,
              last_added: portfolioItems[0]?.added_at,
            },
            riskVolatility,
            monteCarlo,
            portfolioItems,
          }}
          useBackend={true}
        />

        <AIReportModal
          isOpen={showAIReport}
          onClose={() => setShowAIReport(false)}
          fundData={{
            meta: {
              portfolio_size: portfolioItems.length,
              stocks_count: portfolioItems.filter(
                (i) => i.item_type === "stock",
              ).length,
              mutual_funds_count: portfolioItems.filter(
                (i) => i.item_type === "mutual_fund",
              ).length,
              last_added: portfolioItems[0]?.added_at,
            },
            riskVolatility,
            monteCarlo,
            portfolioItems,
          }}
          useBackend={true}
        />

        {/* Buy History Modal */}
        {showHistoryModal && (
          <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
            <div className="bg-[#181f31] rounded-xl max-w-2xl w-full max-h-[90vh] overflow-y-auto border border-gray-700">
              <div className="sticky top-0 bg-[#181f31] border-b border-gray-700 p-6 flex justify-between items-center">
                <div>
                  <h2 className="text-2xl font-bold text-white">Buy History</h2>
                  <p className="text-gray-400">
                    {selectedSymbol?.name} ({selectedSymbol?.symbol})
                  </p>
                </div>
                <button
                  onClick={() => setShowHistoryModal(false)}
                  className="text-gray-400 hover:text-white text-2xl"
                >
                  ×
                </button>
              </div>

              {historyLoading ? (
                <div className="flex justify-center items-center py-12">
                  <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-white"></div>
                </div>
              ) : (
                <div className="p-6">
                  {/* Aggregated Summary */}
                  {historyData.length > 0 && (
                    <div className="bg-white/5 rounded-lg p-4 mb-6 border border-white/10">
                      <h3 className="text-white font-semibold mb-4">
                        Aggregated Summary
                      </h3>
                      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
                        <div>
                          <div className="text-gray-400 text-sm">
                            Total Quantity
                          </div>
                          <div className="text-white font-bold text-lg">
                            {historyData
                              .reduce(
                                (sum, lot) => sum + Number(lot.quantity || 0),
                                0,
                              )
                              .toLocaleString("en-IN", {
                                maximumFractionDigits: 8,
                              })}
                          </div>
                        </div>
                        <div>
                          <div className="text-gray-400 text-sm">
                            Total Invested
                          </div>
                          <div className="text-green-400 font-bold text-lg">
                            ₹
                            {historyData
                              .reduce(
                                (sum, lot) =>
                                  sum +
                                  Number(lot.quantity || 0) *
                                    Number(lot.buy_price || 0),
                                0,
                              )
                              .toLocaleString("en-IN", {
                                maximumFractionDigits: 2,
                              })}
                          </div>
                        </div>
                        <div>
                          <div className="text-gray-400 text-sm">
                            Avg Buy Price
                          </div>
                          <div className="text-white font-bold text-lg">
                            ₹
                            {(
                              historyData.reduce(
                                (sum, lot) =>
                                  sum +
                                  Number(lot.quantity || 0) *
                                    Number(lot.buy_price || 0),
                                0,
                              ) /
                              (historyData.reduce(
                                (sum, lot) => sum + Number(lot.quantity || 0),
                                0,
                              ) || 1)
                            ).toLocaleString("en-IN", {
                              maximumFractionDigits: 4,
                            })}
                          </div>
                        </div>
                        <div>
                          <div className="text-gray-400 text-sm">
                            Current Value
                          </div>
                          <div className="text-blue-400 font-bold text-lg">
                            ₹
                            {(selectedItem?.current_price &&
                            selectedItem.current_price > 0
                              ? historyData.reduce(
                                  (sum, lot) => sum + Number(lot.quantity || 0),
                                  0,
                                ) * selectedItem.current_price
                              : selectedItem?.current_value || 0
                            ).toLocaleString("en-IN", {
                              maximumFractionDigits: 2,
                            })}
                          </div>
                        </div>
                      </div>
                      <div className="mt-4 pt-4 border-t border-white/10">
                        {(() => {
                          const totalInvested = historyData.reduce(
                            (sum, lot) =>
                              sum +
                              Number(lot.quantity || 0) *
                                Number(lot.buy_price || 0),
                            0,
                          );
                          const currentValue =
                            selectedItem?.current_price &&
                            selectedItem.current_price > 0
                              ? historyData.reduce(
                                  (sum, lot) => sum + Number(lot.quantity || 0),
                                  0,
                                ) * selectedItem.current_price
                              : selectedItem?.current_value || 0;
                          const pnl = currentValue - totalInvested;
                          const pnlPct =
                            totalInvested > 0 ? (pnl / totalInvested) * 100 : 0;
                          const isProfitable = pnl >= 0;

                          return (
                            <div className="flex justify-between items-center">
                              <span className="text-gray-300 font-medium">
                                Profit / Loss:
                              </span>
                              <div className="text-right">
                                <div
                                  className={`font-bold text-2xl ${isProfitable ? "text-green-400" : "text-red-400"}`}
                                >
                                  {isProfitable ? "+" : ""}₹
                                  {Math.abs(pnl).toLocaleString("en-IN", {
                                    maximumFractionDigits: 2,
                                  })}
                                </div>
                                <div
                                  className={`text-sm font-semibold ${isProfitable ? "text-green-300" : "text-red-300"}`}
                                >
                                  {isProfitable ? "+" : ""}
                                  {pnlPct.toFixed(2)}%
                                </div>
                              </div>
                            </div>
                          );
                        })()}
                      </div>
                    </div>
                  )}

                  {/* Lot Details Table */}
                  <h3 className="text-white font-semibold mb-4">
                    Individual Lots
                  </h3>
                  {historyData.length > 0 ? (
                    <div className="overflow-x-auto">
                      <table className="w-full text-sm">
                        <thead>
                          <tr className="border-b border-gray-700">
                            <th className="text-left py-3 px-4 text-gray-400 font-medium">
                              Buy Date
                            </th>
                            <th className="text-right py-3 px-4 text-gray-400 font-medium">
                              Quantity
                            </th>
                            <th className="text-right py-3 px-4 text-gray-400 font-medium">
                              Buy Price
                            </th>
                            <th className="text-right py-3 px-4 text-gray-400 font-medium">
                              Invested
                            </th>
                            <th className="text-center py-3 px-4 text-gray-400 font-medium">
                              Action
                            </th>
                          </tr>
                        </thead>
                        <tbody>
                          {historyData.map((lot) => (
                            <tr
                              key={lot.id}
                              className="border-b border-gray-700 hover:bg-white/5"
                            >
                              <td className="py-3 px-4 text-white">
                                {new Date(lot.buy_date).toLocaleDateString()}
                              </td>
                              <td className="text-right py-3 px-4 text-white">
                                {Number(lot.quantity).toLocaleString("en-IN", {
                                  maximumFractionDigits: 8,
                                })}
                              </td>
                              <td className="text-right py-3 px-4 text-white">
                                ₹
                                {Number(lot.buy_price).toLocaleString("en-IN", {
                                  maximumFractionDigits: 4,
                                })}
                              </td>
                              <td className="text-right py-3 px-4 text-green-400">
                                ₹
                                {(
                                  Number(lot.quantity) * Number(lot.buy_price)
                                ).toLocaleString("en-IN", {
                                  maximumFractionDigits: 2,
                                })}
                              </td>
                              <td className="text-center py-3 px-4">
                                <button
                                  onClick={() => handleDeleteLot(lot.id)}
                                  className="text-red-400 hover:text-red-300 text-sm font-medium transition-colors"
                                >
                                  Delete
                                </button>
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  ) : (
                    <div className="text-center py-6 text-gray-400">
                      No buy history found
                    </div>
                  )}
                </div>
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
