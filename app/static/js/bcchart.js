//
// Prep data for chart
//
var pred = [];
var right_cnt_binned = [];
var wrong_cnt_binned = [];
var right_uni_binned = [];
var wrong_uni_binned = [];
var binlabels = [];
var data;


function makeHistoData(max_x = 7000, bin_cnt = 100) {
    var binsize = Math.floor(Math.ceil((max_x / bin_cnt)/100)*100);
    var cur_bin = -1;
    pred = [];
    right_cnt_binned = [];
    wrong_cnt_binned = [];
    right_uni_binned = [];
    wrong_uni_binned = [];
    binlabels = [];
    
    // calc for every kanji (could be more cost efficient)
    for (var x = 0; x <= max_x + binsize; x++) {
        y = 1 / (1 + 2**(in_t * (x - in_a)));
        pred.push(y);
        // push new bins as needed
        if (!(x % binsize)){
            if (cur_bin >= 0){
                right_cnt_binned[cur_bin] = Math.round(right_cnt_binned[cur_bin]);
                wrong_cnt_binned[cur_bin] = Math.round(wrong_cnt_binned[cur_bin]);
                right_uni_binned[cur_bin] = Math.round(right_uni_binned[cur_bin]);
                wrong_uni_binned[cur_bin] = Math.round(wrong_uni_binned[cur_bin]);
            }
            cur_bin++;
            binlabels[cur_bin] = x;
            right_cnt_binned.push(0);
            wrong_cnt_binned.push(0);
            right_uni_binned.push(0);
            wrong_uni_binned.push(0);
        }
        right_cnt_binned[cur_bin] += y * counts[x];
        wrong_cnt_binned[cur_bin] += (1 - y) * counts[x];
        right_uni_binned[cur_bin] += y;
        wrong_uni_binned[cur_bin] += (1 - y);
    }
    right_cnt_binned[cur_bin] = Math.round(right_cnt_binned[cur_bin]);
    wrong_cnt_binned[cur_bin] = Math.round(wrong_cnt_binned[cur_bin]);
    right_uni_binned[cur_bin] = Math.round(right_uni_binned[cur_bin]);
    wrong_uni_binned[cur_bin] = Math.round(wrong_uni_binned[cur_bin]);
    
    data = {
        labels: binlabels,
        datasets: [{
            type: 'bar',
            label: 'Unknown',
            backgroundColor: '#B40020',
            data: wrong_cnt_binned
        },{
            type: 'bar',
            label: 'Known',
            backgroundColor: '#267f00',
            data: right_cnt_binned
        }]
    };
};

//
// Make the chart! (find obj in dom and put a chart there)
//

function makeChart(){
return new Chart(mainctx, {
    type: 'bar',
    data: data,
    options: {
        responsive: true,
        maintainAspectRatio: false,
        scales: {
            xAxes: [{}],
            yAxes: scaletype
        },
        legend: {
            display: false
        },
        layout: {
            padding: {
                top: 30
            }
        },
        animation: {
            duration: 0
        }
    }
});};
