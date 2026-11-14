#include "rectangleguillotine/tree_search.cpp"
#include "packingsolver/rectangleguillotine/instance_builder.hpp"
#include <fstream>
#include <chrono>
#include <algorithm>
using namespace packingsolver;
using namespace packingsolver::rectangleguillotine;
using J = nlohmann::json;
using N = std::shared_ptr<BranchingScheme::Node>;

// Resume the original branching scheme without rebuilding or relaxing a prefix.
// IBS depth is relative to the selected child; original absolute depth remains
// stored in each node and used by the original feasibility and bound methods.
struct ResumeScheme: BranchingScheme {
    using BranchingScheme::BranchingScheme;
    N seed;
    const N root() const { return seed ? seed : BranchingScheme::root(); }
    treesearchsolver::Depth depth(const N& n) const {
        return n->number_of_items - (seed ? seed->number_of_items : 0);
    }
};

J snapshot(const N& n) {
    return J{{"items",n->number_of_items},{"bins",n->number_of_bins},
        {"item_area",n->item_area},{"waste",n->waste},{"current_area",n->current_area},
        {"front",{n->x1_prev,n->x1_curr,n->x3_curr,n->y2_prev,n->y2_curr}},
        {"cut_limits",{n->x1_max,n->y2_max,n->z1,n->z2}},
        {"action",{n->item_type_id_1,n->item_type_id_2,n->df}},
        {"pos_stack",n->pos_stack}};
}

std::vector<double> features(const Instance& inst,const N& n) {
    const auto& b=inst.bin_type(0);
    const double W=b.rect.h,H=b.rect.w,A=W*H,total=inst.item_area();
    const double mean=total/inst.number_of_items();
    const double rate=n->current_area>0?(double)n->waste/n->current_area:0;
    const double smooth=rate<.02?.01+rate/2:rate;
    return {n->item_area>0?smooth/n->item_area*n->number_of_items*mean:0,
        n->waste/A,n->item_area/total,(double)n->number_of_items/inst.number_of_items(),
        (double)n->number_of_bins,n->x1_prev/W,n->x1_curr/W,n->x3_curr/W,
        n->y2_prev/H,n->y2_curr/H,(n->x1_curr-n->x3_curr)/W,
        (H-n->y2_curr)/H,(double)n->df,(double)n->z1,(double)n->z2};
}

int main(int argc,char**argv) {
 try {
    if(argc!=8)throw std::runtime_error("items bins seconds prefix_depth child_rank certificate output_json");
    auto start=std::chrono::steady_clock::now();
    InstanceBuilder b;b.read_item_types(argv[1]);b.read_bin_types(argv[2]);
    b.set_bin_types_infinite_copies();b.set_objective(Objective::BinPacking);
    b.set_number_of_stages(3);b.set_cut_type(CutType::NonExact);
    b.set_first_stage_orientation(CutOrientation::Horizontal);
    b.set_item_types_oriented();b.set_cut_thickness(0);auto inst=b.build();
    BranchingScheme::Parameters bp;bp.guide_id=1;bp.first_stage_orientation=CutOrientation::Horizontal;
    ResumeScheme bs(inst,bp);
    N parent=bs.root();J path=J::array();
    int target=std::stoi(argv[4]);
    while(parent->number_of_items<target&&!bs.leaf(parent)) {
        auto cs=bs.children(parent);if(cs.empty())throw std::runtime_error("empty prefix children");
        std::sort(cs.begin(),cs.end(),[&](const N&a,const N&b){return bs(a,b);});
        parent=cs.front();path.push_back(snapshot(parent));
    }
    auto cs=bs.children(parent);if(cs.empty())throw std::runtime_error("no candidate children");
    std::sort(cs.begin(),cs.end(),[&](const N&a,const N&b){return bs(a,b);});
    int rank=std::min<int>(std::stoi(argv[5]),cs.size()-1);bs.seed=cs[rank];
    J j={{"prefix",path},{"parent",snapshot(parent)},{"child",snapshot(bs.seed)},
        {"rank",rank},{"requested_rank",std::stoi(argv[5])},{"candidate_count",cs.size()},
        {"features",features(inst,bs.seed)},
        {"branch_area_lower_bound",(inst.item_area()+bs.seed->waste+inst.bin_type(0).rect.area()-1)/inst.bin_type(0).rect.area()}};
    J remaining=J::array();
    for(StackId s=0;s<inst.number_of_stacks();++s)
        for(ItemPos p=bs.seed->pos_stack[s];p<inst.stack_size(s);++p){
            auto id=inst.item(s,p);const auto&it=inst.item_type(id);
            remaining.push_back({id,it.rect.w,it.rect.h});
        }
    j["remaining_items"]=remaining;
    treesearchsolver::IterativeBeamSearch2Parameters<ResumeScheme> p;
    p.verbosity_level=0;p.growth_factor=2;
    p.timer.set_time_limit(std::stod(argv[3]));
    auto search_start=std::chrono::steady_clock::now();
    auto out=treesearchsolver::iterative_beam_search_2(bs,p);
    j["search_wall_s"]=std::chrono::duration<double>(std::chrono::steady_clock::now()-search_start).count();
    N best=out.solution_pool.best();
    j["bins"]=nullptr;j["status"]="unknown";j["prefix_verified"]=false;
    if(bs.leaf(best)) {
        auto sol=bs.to_solution(best);
        if(!sol.full()||!sol.feasible())throw std::runtime_error("invalid completed rollout");
        N ancestor=best;while(ancestor&&ancestor!=bs.seed)ancestor=ancestor->parent;
        if(ancestor!=bs.seed)throw std::runtime_error("rollout escaped fixed prefix");
        sol.write(argv[6]);j["bins"]=sol.number_of_bins();j["status"]="feasible_upper_bound";j["prefix_verified"]=true;
    }
    j["expanded"]=out.number_of_nodes_expanded;j["max_queue"]=out.maximum_size_of_the_queue;
    j["wall_s"]=std::chrono::duration<double>(std::chrono::steady_clock::now()-start).count();
    std::ofstream f(argv[7]);f<<j.dump(2);std::cout<<j.dump()<<std::endl;
 }catch(const std::exception&e){std::cerr<<e.what()<<std::endl;return 1;}
}
