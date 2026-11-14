"""Apply isolated scoring interface to a pinned remote copy; never changes pristine."""
import pathlib,shutil
root=pathlib.Path('/content/native-study');repo=root/'pristine';fork=root/'patched'
shutil.copytree(repo/'src/rectangleguillotine',fork/'src/rectangleguillotine',dirs_exist_ok=True)
p=fork/'src/rectangleguillotine/tree_search.hpp';s=p.read_text()
s=s.replace('#include <array>','#include <array>\n#include "native_scoring.hpp"')
s=s.replace('NodeId id = -1;','NodeId id = -1;\n        double research_score = 0;')
s=s.replace('GuideId guide_id = 0;','GuideId guide_id = 0;\n        std::array<double,6> research_weights = {{0,1,0,0,0,0}};')
s=s.replace('private:\n','private:\n    double research_evaluate(const Node& node) const;\n',1)
s=s.replace('switch (parameters_.guide_id) {','''if (parameters_.guide_id == 20) {
        if (node_1->research_score != node_2->research_score)
            return node_1->research_score < node_2->research_score;
        return node_1->id < node_2->id;
    }
    switch (parameters_.guide_id >= 100 ? parameters_.guide_id - 100 : parameters_.guide_id) {''')
s=s.replace('std::vector<GuideId> guides;','std::vector<GuideId> guides;\n    std::array<double,6> research_weights = {{0,1,0,0,0,0}};')
marker='bool BranchingScheme::operator()('
score=r'''double BranchingScheme::research_evaluate(const Node& n) const
{
    if(n.item_area<=0 || n.current_area<=0 || n.number_of_bins<=0)return 0;
    const auto& oriented=instance(n.first_stage_orientation);
    const auto& bin=oriented.bin_type(bin_type_ids_[n.number_of_bins-1]);
    const double A=bin.rect.area(),mean=(double)instance_.item_area()/instance_.number_of_items();
    const double rate=(double)n.waste/n.current_area;
    const double smooth=rate<.02?.01+rate/2:rate;
    std::array<double,6> f={{(double)n.waste/n.item_area,smooth/n.item_area*n.number_of_items*mean,(double)n.waste/A,0,0,-(double)n.number_of_items/instance_.number_of_items()}};
    if(parameters_.research_weights[3]!=0 || parameters_.research_weights[4]!=0) {
        // Three current frontier regions, in the solver's oriented coordinates.
        // Cuts can still move: these are predictive features, NOT hard pruning.
        const std::array<Length,3> widths={{std::max<Length>(0,n.x1_curr-n.x3_curr),std::max<Length>(0,n.x1_curr-n.x1_prev),std::max<Length>(0,bin.rect.w-n.x1_curr)}};
        const std::array<Length,3> heights={{std::max<Length>(0,n.y2_curr-n.y2_prev),std::max<Length>(0,bin.rect.h-n.y2_curr),bin.rect.h}};
        std::array<bool,3> fits={{false,false,false}};double incompatible=0,remaining=0;
        for(StackId st=0;st<oriented.number_of_stacks();++st)for(ItemPos pos=n.pos_stack[st];pos<oriented.stack_size(st);++pos){
            const auto&it=oriented.item_type(oriented.item(st,pos));bool any=false;
            for(int k=0;k<3;++k)if(it.rect.w<=widths[k]&&it.rect.h<=heights[k]){fits[k]=true;any=true;}
            remaining+=it.rect.area();if(!any)incompatible+=it.rect.area();
        }
        for(int k=0;k<3;++k)if(!fits[k])f[3]+=(double)widths[k]*heights[k]/A;
        f[4]=remaining>0?incompatible/remaining:0;
    }
    return native_research_score(f,parameters_.research_weights);
}

'''
assert s.count(marker)==1;s=s.replace(marker,score+marker);p.write_text(s)
p=fork/'src/rectangleguillotine/tree_search.cpp';s=p.read_text();marker='node.cutting_cost += instance.parameters().waste_cost * node.waste;';assert s.count(marker)==1;s=s.replace(marker,marker+'\n    if (parameters_.guide_id == 20) node.research_score = research_evaluate(node);')
marker='branching_scheme_parameters.guide_id = guide_id;';assert s.count(marker)==1;s=s.replace(marker,marker+'\n                branching_scheme_parameters.research_weights = parameters.research_weights;');p.write_text(s)
shutil.copy('/content/native-study/build-src/native_scoring.hpp',fork/'src/native_scoring.hpp')
print('PATCH_APPLIED')
