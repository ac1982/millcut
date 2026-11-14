#include "rectangleguillotine/tree_search.cpp"
#include "packingsolver/rectangleguillotine/instance_builder.hpp"
#include <iostream>
#include <chrono>
#include <sstream>
#include <cmath>
using namespace packingsolver;
using namespace packingsolver::rectangleguillotine;
int main(int argc,char**argv) {
 try {
  if(argc<8)throw std::runtime_error("items bins seconds guides mode queue certificate [six weights]");
  const auto start=std::chrono::steady_clock::now();
  InstanceBuilder b;b.read_item_types(argv[1]);b.read_bin_types(argv[2]);b.set_bin_types_infinite_copies();b.set_objective(Objective::BinPacking);b.set_number_of_stages(3);b.set_cut_type(CutType::NonExact);b.set_first_stage_orientation(CutOrientation::Horizontal);b.set_item_types_oriented();b.set_cut_thickness(0);auto inst=b.build();
  TreeSearchParameters p;p.verbosity_level=0;
  std::stringstream gs(argv[4]);std::string g;while(std::getline(gs,g,','))p.guides.push_back(std::stoi(g));
  if(std::string(argv[5])=="deterministic")p.optimization_mode=OptimizationMode::NotAnytimeDeterministic;
  p.not_anytime_tree_search_queue_size=std::stoll(argv[6]);
#ifdef NATIVE_RESEARCH
  if(argc>=14)for(int i=0;i<6;++i){p.research_weights[i]=std::stod(argv[8+i]);if(!std::isfinite(p.research_weights[i]))throw std::runtime_error("non-finite weight");}
#endif
  const double seconds=std::stod(argv[3]);
  const double elapsed=std::chrono::duration<double>(std::chrono::steady_clock::now()-start).count();
  p.timer.set_time_limit(std::max(.001,seconds-elapsed));
  auto out=tree_search(inst,p);const auto&sol=out.solution_pool.best();
  double wall=std::chrono::duration<double>(std::chrono::steady_clock::now()-start).count();
  bool valid=sol.full()&&sol.feasible();if(valid)sol.write(argv[7]);
  nlohmann::json j={{"bins",valid?sol.number_of_bins():-1},{"valid",valid},{"wall_s",wall},{"bound",out.bin_packing_bound},{"guides",p.guides}};std::cout<<j.dump()<<std::endl;return valid?0:2;
 }catch(const std::exception&e){std::cerr<<e.what()<<std::endl;return 1;}
}
