#include "mill_cxx23.hpp"

#include <cstdint>
#include <sstream>
#include <string>
#include <string_view>

#ifdef __EMSCRIPTEN__
#include <emscripten/emscripten.h>
#define MILL_EXPORT EMSCRIPTEN_KEEPALIVE
#else
#define MILL_EXPORT
#endif

namespace {

// Single-threaded Wasm Worker static response buffer
std::string g_wasm_response;

std::string escape_json_msg(std::string_view sv) {
    std::string out;
    out.reserve(sv.size() + 8);
    for (char ch : sv) {
        switch (ch) {
            case '"': out += "\\\""; break;
            case '\\': out += "\\\\"; break;
            case '\n': out += "\\n"; break;
            case '\r': out += "\\r"; break;
            case '\t': out += "\\t"; break;
            default: out.push_back(ch); break;
        }
    }
    return out;
}

} // namespace

extern "C" {

MILL_EXPORT const char* wasm_engine_info() {
    std::ostringstream oss;
    oss << "{"
        << "\"engine\":\"Mill C++23 Paper Guillotine Optimizer\","
        << "\"standard\":\"C++23\","
        << "\"cplusplus\":" << __cplusplus << ","
        << "\"zero_dependencies\":true,"
        << "\"constraints\":\"3-Stage NonExact Horizontal Guillotine (Oriented, Zero-Kerf)\""
        << "}";
    g_wasm_response = oss.str();
    return g_wasm_response.c_str();
}

MILL_EXPORT const char* wasm_solve_order(
    const char* bins_csv,
    const char* items_csv,
    int mode_id,
    double budget_s,
    uint32_t seed,
    const char* order_name) {
    std::string_view b_sv = bins_csv ? bins_csv : "";
    std::string_view i_sv = items_csv ? items_csv : "";
    std::string_view name_sv = (order_name && order_name[0] != '\0') ? order_name : "custom_order";

    auto ord = mill::parse_order_csvs(b_sv, i_sv);
    if (!ord) {
        g_wasm_response =
            "{\"ok\":false,\"error_code\":\"" + escape_json_msg(ord.error().code) +
            "\",\"error\":\"" + escape_json_msg(ord.error().message) + "\"}";
        return g_wasm_response.c_str();
    }

    mill::SolveMode mode = mill::SolveMode::MillRouter;
    if (mode_id == 1) mode = mill::SolveMode::ColStripOnly;
    else if (mode_id == 2) mode = mill::SolveMode::BaselineGreedy;
    else if (mode_id == 3) mode = mill::SolveMode::TightShelf;

    auto res = mill::solve_order(*ord, mode, budget_s, seed);
    if (!res) {
        g_wasm_response =
            "{\"ok\":false,\"error_code\":\"" + escape_json_msg(res.error().code) +
            "\",\"error\":\"" + escape_json_msg(res.error().message) + "\"}";
        return g_wasm_response.c_str();
    }

    g_wasm_response = mill::serialize_solve_json(*res, name_sv);
    return g_wasm_response.c_str();
}

MILL_EXPORT const char* wasm_verify_certificate(
    const char* bins_csv,
    const char* items_csv,
    const char* cut_tree_csv,
    const char* order_name) {
    std::string_view b_sv = bins_csv ? bins_csv : "";
    std::string_view i_sv = items_csv ? items_csv : "";
    std::string_view t_sv = cut_tree_csv ? cut_tree_csv : "";
    std::string_view name_sv = (order_name && order_name[0] != '\0') ? order_name : "verified_order";

    auto ord = mill::parse_order_csvs(b_sv, i_sv);
    if (!ord) {
        g_wasm_response =
            "{\"valid\":false,\"error_code\":\"" + escape_json_msg(ord.error().code) +
            "\",\"error\":\"" + escape_json_msg(ord.error().message) + "\"}";
        return g_wasm_response.c_str();
    }

    auto rep = mill::verify_cut_tree_csv(*ord, t_sv);
    if (!rep) {
        g_wasm_response =
            "{\"valid\":false,\"error_code\":\"" + escape_json_msg(rep.error().code) +
            "\",\"error\":\"" + escape_json_msg(rep.error().message) + "\"}";
        return g_wasm_response.c_str();
    }

    g_wasm_response = mill::serialize_verify_json(*rep, name_sv);
    return g_wasm_response.c_str();
}

MILL_EXPORT const char* wasm_solve_pareto(
    const char* bins_csv,
    const char* items_csv,
    double sheet_cost_rmb,
    double machine_rate_hr,
    double setup_min,
    double stroke_sec,
    int max_stack,
    double budget_s,
    uint32_t seed,
    const char* order_name) {
    std::string_view b_sv = bins_csv ? bins_csv : "";
    std::string_view i_sv = items_csv ? items_csv : "";
    std::string_view name_sv = (order_name && order_name[0] != '\0') ? order_name : "custom_order";

    auto ord = mill::parse_order_csvs(b_sv, i_sv);
    if (!ord) {
        g_wasm_response =
            "{\"ok\":false,\"error_code\":\"" + escape_json_msg(ord.error().code) +
            "\",\"error\":\"" + escape_json_msg(ord.error().message) + "\"}";
        return g_wasm_response.c_str();
    }

    mill::CostModel cost;
    cost.sheet_cost_rmb = sheet_cost_rmb > 0.0 ? sheet_cost_rmb : 2.00;
    cost.machine_rate_rmb_per_hour = machine_rate_hr >= 0.0 ? machine_rate_hr : 180.0;
    cost.setup_minutes_per_pattern = setup_min >= 0.0 ? setup_min : 6.0;
    cost.stroke_seconds = stroke_sec >= 0.0 ? stroke_sec : 9.0;
    cost.max_stack_sheets = max_stack >= 1 ? max_stack : 200;

    auto res = mill::solve_pareto_order(*ord, cost, budget_s, seed);
    if (!res) {
        g_wasm_response =
            "{\"ok\":false,\"error_code\":\"" + escape_json_msg(res.error().code) +
            "\",\"error\":\"" + escape_json_msg(res.error().message) + "\"}";
        return g_wasm_response.c_str();
    }

    g_wasm_response = mill::serialize_pareto_json(*res, name_sv);
    return g_wasm_response.c_str();
}

} // extern "C"
