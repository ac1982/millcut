#include "mill_cxx23.hpp"

#include <cstdlib>
#include <fstream>
#include <iostream>
#include <sstream>
#include <string>
#include <string_view>

namespace {

std::expected<std::string, std::string> read_text_file(const std::string& path) {
    std::ifstream ifs(path, std::ios::binary);
    if (!ifs) {
        return std::unexpected("Cannot open file: " + path);
    }
    std::ostringstream oss;
    oss << ifs.rdbuf();
    return oss.str();
}

bool write_text_file(const std::string& path, std::string_view content) {
    std::ofstream ofs(path, std::ios::binary);
    if (!ofs) return false;
    ofs.write(content.data(), static_cast<std::streamsize>(content.size()));
    return static_cast<bool>(ofs);
}

void print_usage() {
    std::cerr
        << "Usage:\n"
        << "  mill_cxx23_cli solve --bins <bins.csv> --items <items.csv> [--mode router|colstrip|baseline|guide1]\n"
        << "                       [--budget <sec>] [--seed <int>] [--order-name <id>]\n"
        << "                       [--out-csv <cut_tree.csv>] [--out-json <result.json>]\n"
        << "  mill_cxx23_cli verify --bins <bins.csv> --items <items.csv> --tree <cut_tree.csv> [--order-name <id>]\n"
        << "  mill_cxx23_cli info\n";
}

} // namespace

int main(int argc, char** argv) {
    if (argc < 2) {
        print_usage();
        return 2;
    }

    const std::string cmd = argv[1];
    if (cmd == "info") {
        std::cout << "{\"standard\":\"C++23\",\"cplusplus\":" << __cplusplus
                  << ",\"zero_dependencies\":true}\n";
        return 0;
    }

    std::string bins_path;
    std::string items_path;
    std::string tree_path;
    std::string out_csv_path;
    std::string out_json_path;
    std::string mode_str = "router";
    std::string order_name = "order";
    double budget_s = 10.0;
    uint32_t seed = 501;

    for (int i = 2; i < argc; ++i) {
        std::string arg = argv[i];
        auto next_val = [&]() -> std::string {
            if (i + 1 >= argc) {
                std::cerr << "Missing value after " << arg << "\n";
                std::exit(2);
            }
            return argv[++i];
        };
        if (arg == "--bins") bins_path = next_val();
        else if (arg == "--items") items_path = next_val();
        else if (arg == "--tree") tree_path = next_val();
        else if (arg == "--out-csv") out_csv_path = next_val();
        else if (arg == "--out-json") out_json_path = next_val();
        else if (arg == "--mode") mode_str = next_val();
        else if (arg == "--order-name") order_name = next_val();
        else if (arg == "--budget") budget_s = std::stod(next_val());
        else if (arg == "--seed") seed = static_cast<uint32_t>(std::stoul(next_val()));
        else {
            std::cerr << "Unknown argument: " << arg << "\n";
            print_usage();
            return 2;
        }
    }

    if (bins_path.empty() || items_path.empty()) {
        print_usage();
        return 2;
    }

    auto bins_txt = read_text_file(bins_path);
    if (!bins_txt) {
        std::cerr << bins_txt.error() << "\n";
        return 1;
    }
    auto items_txt = read_text_file(items_path);
    if (!items_txt) {
        std::cerr << items_txt.error() << "\n";
        return 1;
    }

    auto ord = mill::parse_order_csvs(*bins_txt, *items_txt);
    if (!ord) {
        std::cerr << "{\"ok\":false,\"error_code\":\"" << ord.error().code
                  << "\",\"error\":\"" << ord.error().message << "\"}\n";
        return 1;
    }

    if (cmd == "verify") {
        if (tree_path.empty()) {
            print_usage();
            return 2;
        }
        auto tree_txt = read_text_file(tree_path);
        if (!tree_txt) {
            std::cerr << tree_txt.error() << "\n";
            return 1;
        }
        auto rep = mill::verify_cut_tree_csv(*ord, *tree_txt);
        if (!rep) {
            std::cout << "{\"valid\":false,\"error_code\":\"" << rep.error().code
                      << "\",\"error\":\"" << rep.error().message << "\"}\n";
            return 1;
        }
        std::cout << mill::serialize_verify_json(*rep, order_name) << "\n";
        return 0;
    }

    if (cmd == "solve") {
        mill::SolveMode mode = mill::SolveMode::MillRouter;
        if (mode_str == "colstrip") mode = mill::SolveMode::ColStripOnly;
        else if (mode_str == "baseline") mode = mill::SolveMode::BaselineGreedy;
        else if (mode_str == "guide1") mode = mill::SolveMode::TightShelf;

        auto res = mill::solve_order(*ord, mode, budget_s, seed);
        if (!res) {
            std::cerr << "{\"ok\":false,\"error_code\":\"" << res.error().code
                      << "\",\"error\":\"" << res.error().message << "\"}\n";
            return 1;
        }

        if (!out_csv_path.empty()) {
            if (!write_text_file(out_csv_path, res->cut_tree_csv)) {
                std::cerr << "Failed to write " << out_csv_path << "\n";
                return 1;
            }
        }
        std::string js = mill::serialize_solve_json(*res, order_name);
        if (!out_json_path.empty()) {
            if (!write_text_file(out_json_path, js)) {
                std::cerr << "Failed to write " << out_json_path << "\n";
                return 1;
            }
        } else {
            std::cout << js << "\n";
        }
        return 0;
    }

    if (cmd == "pareto") {
        auto res = mill::solve_pareto_order(*ord, mill::CostModel{}, budget_s, seed);
        if (!res) {
            std::cerr << "{\"ok\":false,\"error_code\":\"" << res.error().code
                      << "\",\"error\":\"" << res.error().message << "\"}\n";
            return 1;
        }
        std::string js = mill::serialize_pareto_json(*res, order_name);
        if (!out_json_path.empty()) {
            if (!write_text_file(out_json_path, js)) {
                std::cerr << "Failed to write " << out_json_path << "\n";
                return 1;
            }
        } else {
            std::cout << js << "\n";
        }
        return 0;
    }

    print_usage();
    return 2;
}
