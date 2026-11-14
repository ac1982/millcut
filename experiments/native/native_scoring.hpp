#pragma once
#include <array>
// Pure ranking function; never used as an admissible bound or feasibility test.
inline double native_research_score(const std::array<double,6>&features,const std::array<double,6>&weights) {
    double score=0;for(size_t i=0;i<features.size();++i)score+=features[i]*weights[i];return score;
}
