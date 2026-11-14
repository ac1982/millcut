#include "packingsolver/rectangleguillotine/instance_builder.hpp"
#include "rectangleguillotine/solution_builder.hpp"
#include <random>
#include <chrono>
#include <algorithm>
#include <fstream>
#include <cmath>
#include <iostream>
using namespace packingsolver;
using namespace packingsolver::rectangleguillotine;
using Col=std::vector<ItemTypeId>;
struct Row{int bin;std::vector<Col> cols;};
using State=std::vector<Row>;
struct Metric{double energy,violation;};
struct Operator {
 const Instance&inst;int W,H,K;std::mt19937 rng;
 int pick(int n){return std::uniform_int_distribution<int>(0,n-1)(rng);}
 double unit(){return std::uniform_real_distribution<double>(0,1)(rng);}
 int cw(const Col&c)const{int w=0;for(auto i:c)w=std::max(w,(int)inst.item_type(i).rect.w);return w;}
 int ch(const Col&c)const{int h=0;for(auto i:c)h+=inst.item_type(i).rect.h;return h;}
 int rh(const Row&r)const{int h=0;for(auto&c:r.cols)h=std::max(h,ch(c));return h;}
 int rw(const Row&r)const{int w=0;for(auto&c:r.cols)w+=cw(c);return w;}
 void clean(State&s){for(auto&r:s)r.cols.erase(std::remove_if(r.cols.begin(),r.cols.end(),[](const Col&c){return c.empty();}),r.cols.end());s.erase(std::remove_if(s.begin(),s.end(),[](const Row&r){return r.cols.empty();}),s.end());}
 Metric metric(const State&s)const{
   std::vector<int> heights(K,0);double v=0,foot=0,totalh=0;
   for(auto&r:s){int w=rw(r),h=rh(r);heights[r.bin]+=h;v+=std::max(0,w-W)*h;foot+=(double)w*h;}
   for(int h:heights){v+=std::max(0,h-H)*W;totalh+=h;}
   return {v/(W*H)+.02*totalh/H+.005*foot/(W*H),v};
 }
 State read(const Solution&sol){
   State s;int bid=0;
   for(int bi=0;bi<sol.number_of_different_bins();++bi){auto&b=sol.bin(bi);
     for(int copy=0;copy<b.copies;++copy,++bid){
       std::map<int,std::map<int,Col>> rows;
       for(int j=0;j<(int)b.nodes.size();++j){auto&n=b.nodes[j];if(n.f<0||n.item_type_id<0)continue;
         int r=j,c=j;while(b.nodes[r].d>1)r=b.nodes[r].f;while(b.nodes[c].d>2)c=b.nodes[c].f;
         rows[r][c].push_back(n.item_type_id);
       }
       for(auto&rr:rows){Row r{bid,{}};for(auto&cc:rr.second)r.cols.push_back(cc.second);s.push_back(r);}
     }
   }return s;
 }
 State evacuate(State s){
   std::vector<long long> areas(K+1,0);for(auto&r:s)for(auto&c:r.cols)for(auto i:c)areas[r.bin]+=inst.item_type(i).rect.area();
   int remove=std::min_element(areas.begin(),areas.end())-areas.begin();std::vector<Col> loose;State keep;
   for(auto&r:s){if(r.bin==remove)for(auto&c:r.cols)loose.push_back(c);else{if(r.bin>remove)--r.bin;keep.push_back(r);}}
   std::shuffle(loose.begin(),loose.end(),rng);
   for(auto&c:loose){State best;double e=1e100;
     for(int r=0;r<(int)keep.size()+K;++r){State q=keep;if(r<(int)keep.size())q[r].cols.push_back(c);else q.push_back({r-(int)keep.size(),{c}});
       double v=metric(q).energy;if(v<e){e=v;best=std::move(q);}}
     keep=std::move(best);
   }return keep;
 }
 int source(const State&s){
   if(unit()<.3)return pick(s.size());std::vector<int> heights(K,0);for(auto&r:s)heights[r.bin]+=rh(r);
   std::vector<double>w;for(auto&r:s)w.push_back(1+10.*std::max(0,rw(r)-W)/W+10.*std::max(0,heights[r.bin]-H)/H);
   return std::discrete_distribution<int>(w.begin(),w.end())(rng);
 }
 void mutate(State&s,int op){
   int a=source(s),ac=pick(s[a].cols.size());int b=pick(s.size()),bc=pick(s[b].cols.size());
   if(op==0){ // Whole reusable column to another row, or a new row.
     Col c=s[a].cols[ac];s[a].cols[ac].clear();if(unit()<.2)s.push_back({pick(K),{c}});else s[b].cols.push_back(c);
   }else if(op==1){ // Eject one item into an existing stack; trim widths at stage 4.
     if(a==b&&ac==bc)return;int i=pick(s[a].cols[ac].size());auto id=s[a].cols[ac][i];
     s[a].cols[ac].erase(s[a].cols[ac].begin()+i);s[b].cols[bc].push_back(id);
   }else if(op==2){ // Release an item from its column into a separate column.
     int i=pick(s[a].cols[ac].size());auto id=s[a].cols[ac][i];s[a].cols[ac].erase(s[a].cols[ac].begin()+i);
     if(unit()<.2)s.push_back({pick(K),{{id}}});else s[b].cols.push_back({id});
   }else if(op==3){std::swap(s[a].cols[ac],s[b].cols[bc]);
   }else if(op==4){ // Merge whole subtrees without changing their internal order.
     if(a==b&&ac==bc)return;auto c=s[a].cols[ac];s[a].cols[ac].clear();s[b].cols[bc].insert(s[b].cols[bc].end(),c.begin(),c.end());
   }else if(op==5){ // Split at an existing third-stage cut.
     auto&c=s[a].cols[ac];if(c.size()<2)return;int at=1+pick(c.size()-1);Col tail(c.begin()+at,c.end());c.erase(c.begin()+at,c.end());s[b].cols.push_back(tail);
   }else if(op==6){s[a].bin=pick(K);
   }else { // Exchange individual pieces between two stacks.
     if(a==b&&ac==bc)return;int i=pick(s[a].cols[ac].size()),j=pick(s[b].cols[bc].size());std::swap(s[a].cols[ac][i],s[b].cols[bc][j]);
   }clean(s);
 }
 Solution build(const State&s){
   SolutionBuilder b(inst);
   for(int k=0;k<K;++k){bool any=false;for(auto&r:s)if(r.bin==k)any=true;if(!any)continue;
     b.add_bin(0,1,CutOrientation::Horizontal);int y=0;
     for(auto&r:s)if(r.bin==k){int h=rh(r);b.add_node(1,y+h);int x=0;
       for(auto&c:r.cols){int w=cw(c);b.add_node(2,x+w);int yy=y;
         for(auto i:c){auto&it=inst.item_type(i);yy+=it.rect.h;b.add_node(3,yy);if(it.rect.w<w)b.add_node(4,x+it.rect.w);b.set_last_node_item(i);}x+=w;
       }y+=h;
     }
   }return b.build();
 }
};
int main(int argc,char**argv){try{
 if(argc!=9)throw std::runtime_error("items bins initial_certificate seconds seed anneal|monotone output_certificate output_json");
 auto start=std::chrono::steady_clock::now();InstanceBuilder ib;ib.read_item_types(argv[1]);ib.read_bin_types(argv[2]);ib.set_bin_types_infinite_copies();ib.set_objective(Objective::BinPacking);ib.set_number_of_stages(3);ib.set_cut_type(CutType::NonExact);ib.set_first_stage_orientation(CutOrientation::Horizontal);ib.set_item_types_oriented();ib.set_cut_thickness(0);auto inst=ib.build();
 SolutionBuilder sb(inst);sb.read(argv[3]);auto initial=sb.build();if(!initial.full()||!initial.feasible())throw std::runtime_error("invalid initial solution");
 int K=initial.number_of_bins()-1;if(K<1)throw std::runtime_error("no sheet to eliminate");
 Operator op{inst,(int)inst.bin_type(0).rect.w,(int)inst.bin_type(0).rect.h,K,std::mt19937((unsigned)std::stoul(argv[5]))};
 auto original=op.read(initial); // First verify the compacted representation at the original bin count.
 op.K=K+1;auto compact=op.build(original);if(!compact.full()||!compact.feasible())throw std::runtime_error("subtree extraction changed feasibility");op.K=K;
 State current=op.evacuate(original),best=current;auto cm=op.metric(current),bm=cm;
 long long iterations=0,accepted=0,uphill=0,restarts=0;std::vector<long long> proposed(8,0),accepted_ops(8,0);
 bool anneal=std::string(argv[6])=="anneal",success=cm.violation==0;double budget=std::stod(argv[4]);
 while(!success){
   if((iterations%128)==0&&std::chrono::duration<double>(std::chrono::steady_clock::now()-start).count()>=budget)break;
   int move=op.pick(8);State candidate=current;op.mutate(candidate,move);auto m=op.metric(candidate);++proposed[move];
   double temperature=.04*std::pow(.00125,(iterations%50000)/50000.0),delta=m.energy-cm.energy;
   if(m.violation==0||delta<=0||(anneal&&op.unit()<std::exp(-delta/temperature))){if(delta>1e-12)++uphill;++accepted;++accepted_ops[move];current=std::move(candidate);cm=m;
     if(cm.energy<bm.energy){best=current;bm=cm;}if(cm.violation==0){success=true;best=current;bm=cm;}}
   ++iterations;if(!success&&iterations%50000==0){++restarts;if(op.unit()<.7){current=best;cm=bm;}else{current=op.evacuate(original);cm=op.metric(current);if(cm.violation==0){success=true;best=current;bm=cm;}}}
 }
 nlohmann::json j={{"initial_bins",initial.number_of_bins()},{"target_bins",K},{"success",success},{"iterations",iterations},{"accepted",accepted},{"accepted_uphill",uphill},{"restarts",restarts},{"best_violation_area",bm.violation},{"proposed_by_operator",proposed},{"accepted_by_operator",accepted_ops},{"mode",argv[6]},{"subtree_roundtrip_valid",true}};
 auto snapshot=[](const State&s){nlohmann::json a=nlohmann::json::array();for(auto&r:s)a.push_back({{"bin",r.bin},{"columns",r.cols}});return a;};
 j["initial_state"]=snapshot(original);j["best_state"]=snapshot(best);
 j["best_state_is_feasible"]=success;
 if(success){auto sol=op.build(best);if(!sol.full()||!sol.feasible()||sol.number_of_bins()>K)throw std::runtime_error("invalid final solution");sol.write(argv[7]);j["bins"]=sol.number_of_bins();j["valid"]=true;}else{j["bins"]=initial.number_of_bins();j["valid"]=true;}
 j["wall_s"]=std::chrono::duration<double>(std::chrono::steady_clock::now()-start).count();std::ofstream out(argv[8]);out<<j.dump(2);std::cout<<j.dump()<<std::endl;return 0;
 }catch(const std::exception&e){std::cerr<<e.what()<<std::endl;return 1;}}
