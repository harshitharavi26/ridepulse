"""Render tables and plots from retained measurements; never fill missing trials."""
import argparse
import csv
import json
from pathlib import Path
import re
from statistics import median

import os
os.environ.setdefault('MPLCONFIGDIR',str(Path(__file__).resolve().parents[1]/'work/matplotlib'))
Path(os.environ['MPLCONFIGDIR']).mkdir(parents=True,exist_ok=True)
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt


def summarize(root):
    workers=json.loads((root/'workers.json').read_text())['runs']
    groups={limit:[run for run in workers if run['configured_max_instances']==limit] for limit in (1,2,4,8)}
    if any(len(runs)!=3 for runs in groups.values()):raise ValueError('Expected three completed worker runs at each limit')
    sizes={run['source_rows'] for run in workers}
    if len(sizes)!=1:raise ValueError('Worker trials must use the same input size')
    source_rows=sizes.pop()
    baseline=median(run['seconds'] for run in groups[1])
    worker_summary=[]
    for limit,runs in groups.items():
        seconds=[run['seconds'] for run in runs];rates=[run['rows_per_second'] for run in runs]
        worker_summary.append({'max_instances':limit,'repeats':len(runs),'median_seconds':median(seconds),
            'median_rows_per_second':median(rates),'min_rows_per_second':min(rates),'max_rows_per_second':max(rates),
            'observed_peak_instances':[run['observed_peak_active_instances'] for run in runs],
            'speedup':baseline/median(seconds),'efficiency_vs_configured_max':baseline/median(seconds)/limit})
    api=[]
    for path in sorted((root/'api').glob('*_stats.csv')):
        match=re.fullmatch(r'(cached|bypass)-u(\d+)-r(\d+)_stats.csv',path.name)
        if not match:continue
        with path.open() as source:rows=list(csv.DictReader(source))
        total=next(row for row in rows if row['Name']=='Aggregated')
        counters=json.loads(path.with_name(path.name.replace('_stats.csv','.json')).read_text())
        # Periodic CSV snapshots can precede shutdown by a few responses.
        # The final console summary is printed after runner shutdown.
        lines=re.findall(r'^\s*Aggregated\s+.*$',path.with_name(path.name.replace('_stats.csv','.log')).read_text(),re.M)
        if len(lines)!=2:raise ValueError('Missing final Locust summary: '+path.name)
        main=lines[0].split('|');count_fields=main[0].split();percentiles=lines[1].split()[1:]
        requests_count=int(count_fields[1]);failures=int(count_fields[2].split('(')[0])
        if requests_count!=counters['requests']:raise ValueError('Final stats/correctness counter mismatch: '+path.name)
        api.append({'mode':match[1],'users':int(match[2]),'repeat':int(match[3]),
                    'requests':requests_count,'failures':failures,
                    'rps':float(main[2].split()[0]),
                    'successful_rps':float(main[2].split()[0])*(requests_count-failures)/requests_count,'p50_ms':float(percentiles[0]),'p95_ms':float(percentiles[5]),
                    'average_ms':float(main[1].split()[0]),'counters':counters,
                    'metric_source':'final Locust console summary','periodic_csv_request_count':int(total['Request Count'])})
    api_summary=[]
    for users in (1,5,10,20):
        for mode in ('cached','bypass'):
            runs=[run for run in api if run['users']==users and run['mode']==mode]
            if len(runs)!=3:raise ValueError(f'Missing API trials: {mode}, {users}')
            api_summary.append({'mode':mode,'users':users,'repeats':len(runs),
                **{f'median_{key}':median(run[key] for run in runs) for key in ('rps','successful_rps','p50_ms','p95_ms','average_ms')},
                'min_p95_ms':min(run['p95_ms'] for run in runs),'max_p95_ms':max(run['p95_ms'] for run in runs),
                'requests':sum(run['requests'] for run in runs),'failures':sum(run['failures'] for run in runs),
                'result_mismatches':sum(run['counters'].get('result_mismatches',0) for run in runs),
                'version_drift':sum(run['counters'].get('version_drift',0) for run in runs),
                'cache_hits':sum(run['counters'].get('cache_hit',0) for run in runs)})
    result={'workers':worker_summary,'api':api_summary,'api_trials':api}
    (root/'summary.json').write_text(json.dumps(result,indent=2))
    lines=['# Measured results','',
           'Three repeats per setting. Latency and throughput values below are medians of trial summaries, not pooled percentiles.',
           'Worker plots show the min/max throughput over three runs. API plots show min/max trial p95; these are not confidence intervals.','',
           '| Worker maximum | Observed peak per repeat | Median seconds | Rows/s | Speedup | Efficiency vs maximum |',
           '| --- | --- | ---: | ---: | ---: | ---: |']
    for row in worker_summary:
        lines.append(f"| {row['max_instances']} | {row['observed_peak_instances']} | {row['median_seconds']:.2f} | {row['median_rows_per_second']:.0f} | {row['speedup']:.2f} | {row['efficiency_vs_configured_max']:.1%} |")
    lines.extend(['','| Users | Mode | Requests | Failures | Median successful req/s | Median p50 ms | Median p95 ms |','| ---: | --- | ---: | ---: | ---: | ---: | ---: |'])
    for row in api_summary:
        lines.append(f"| {row['users']} | {row['mode']} | {row['requests']} | {row['failures']} | {row['median_successful_rps']:.2f} | {row['median_p50_ms']:.0f} | {row['median_p95_ms']:.0f} |")
    (root/'summary.md').write_text('\n'.join(lines)+'\n')
    plt.rcParams.update({'font.size':10,'axes.spines.top':False,'axes.spines.right':False})
    fig,axes=plt.subplots(1,2,figsize=(10,4.2),layout='constrained')
    x=[r['max_instances'] for r in worker_summary];y=[r['median_rows_per_second'] for r in worker_summary]
    axes[0].errorbar(x,y,yerr=[[y[i]-r['min_rows_per_second'] for i,r in enumerate(worker_summary)],
                              [r['max_rows_per_second']-y[i] for i,r in enumerate(worker_summary)]],fmt='o-',capsize=4,color='#0f766e')
    axes[0].set(xlabel='Configured maximum worker instances',ylabel='Unique committed rows / second',title=f'Worker throughput ({source_rows:,} rows / trial)',xticks=x)
    axes[1].plot(x,[r['speedup'] for r in worker_summary],'o-',label='Measured',color='#0f766e')
    axes[1].plot(x,x,'--',color='#64748b',label='Ideal scaling')
    axes[1].set(xlabel='Configured maximum worker instances',ylabel='Speedup vs maximum 1',title='Scaling with a fixed shared-core SQL tier',xticks=x)
    axes[1].legend();fig.savefig(root/'worker-scaling.png',dpi=180);plt.close(fig)
    fig,axes=plt.subplots(1,2,figsize=(10,4.2),layout='constrained')
    for mode,color in [('cached','#0f766e'),('bypass','#b45309')]:
        rows=[r for r in api_summary if r['mode']==mode];x=[r['users'] for r in rows];y=[r['median_p95_ms'] for r in rows]
        axes[0].errorbar(x,y,yerr=[[y[i]-r['min_p95_ms'] for i,r in enumerate(rows)],
                                  [r['max_p95_ms']-y[i] for i,r in enumerate(rows)]],fmt='o-',capsize=4,label=mode,color=color)
        axes[1].plot(x,[r['median_successful_rps'] for r in rows],'o-',label=mode,color=color)
    axes[0].set(xlabel='Concurrent Locust users',ylabel='Response time (ms)',title='Median trial p95 latency',yscale='log',xticks=[1,5,10,20])
    axes[1].set(xlabel='Concurrent Locust users',ylabel='Successful requests / second',title='Median successful trial throughput',xticks=[1,5,10,20])
    for ax in axes:ax.legend()
    fig.savefig(root/'api-cache-comparison.png',dpi=180);plt.close(fig)
    print(root/'summary.md')


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--results',type=Path,default=Path('experiments/results'))
    summarize(parser.parse_args().results)
