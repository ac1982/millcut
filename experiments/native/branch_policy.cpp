// Reuse the exact feature definition and native continuation types.
#define main unused_branch_label_main
#include "branch_labels.cpp"
#undef main

struct PolicyScheme: ResumeScheme {
    using ResumeScheme::ResumeScheme;
    std::vector<double> weights;
    std::vector<N> children(const N& parent) const {
        auto cs=ResumeScheme::children(parent);
        if(!weights.empty())for(auto&n:cs){
            const auto f=features(instance(),n);n->research_score=0;
            for(size_t i=0;i<f.size();++i)n->research_score-=weights[i]*f[i];
        }
        return cs;
    }
    bool operator()(const N&a,const N&b) const {
        if(weights.empty())return ResumeScheme::operator()(a,b);
        if(a->research_score!=b->research_score)return a->research_score<b->research_score;
        return a->id<b->id;
    }
};

int main(int argc,char**argv) {
 try {
    if(argc!=6)throw std::runtime_error("items bins seconds model_json_or_native certificate");
    auto start=std::chrono::steady_clock::now();
    InstanceBuilder b;b.read_item_types(argv[1]);b.read_bin_types(argv[2]);
    b.set_bin_types_infinite_copies();b.set_objective(Objective::BinPacking);
    b.set_number_of_stages(3);b.set_cut_type(CutType::NonExact);
    b.set_first_stage_orientation(CutOrientation::Horizontal);b.set_item_types_oriented();b.set_cut_thickness(0);
    auto inst=b.build();BranchingScheme::Parameters bp;bp.guide_id=1;bp.first_stage_orientation=CutOrientation::Horizontal;
    PolicyScheme bs(inst,bp);
    if(std::string(argv[4])!="native"){
        J m;std::ifstream f(argv[4]);f>>m;
        auto w=m.at("weights").get<std::vector<double>>();auto scale=m.at("scale").get<std::vector<double>>();
        if(w.size()!=15||scale.size()!=15)throw std::runtime_error("invalid model dimension");
        for(size_t i=0;i<w.size();++i){double v=w[i]/scale[i];if(!std::isfinite(v))throw std::runtime_error("nonfinite score weight");bs.weights.push_back(v);}
    }
    treesearchsolver::IterativeBeamSearch2Parameters<PolicyScheme> p;p.verbosity_level=0;p.growth_factor=2;
    const double elapsed=std::chrono::duration<double>(std::chrono::steady_clock::now()-start).count();
    p.timer.set_time_limit(std::max(.001,std::stod(argv[3])-elapsed));
    auto out=treesearchsolver::iterative_beam_search_2(bs,p);const auto&best=out.solution_pool.best();
    J j={{"bins",nullptr},{"valid",false},{"expanded",out.number_of_nodes_expanded}};
    if(bs.leaf(best)){
        auto sol=bs.to_solution(best);if(!sol.full()||!sol.feasible())throw std::runtime_error("invalid policy output");
        sol.write(argv[5]);j["bins"]=sol.number_of_bins();j["valid"]=true;
    }
    j["wall_s"]=std::chrono::duration<double>(std::chrono::steady_clock::now()-start).count();
    std::cout<<j.dump()<<std::endl;
 }catch(const std::exception&e){std::cerr<<e.what()<<std::endl;return 1;}
}
