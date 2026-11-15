"""Derive smaller immutable CSV batches from generation-pinned source batches."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import csv
from datetime import datetime,timezone
import hashlib
import io
import itertools
import json
from pathlib import Path

from google.cloud import storage
from google.oauth2.credentials import Credentials
from cloud_support import PROJECT,BUCKET,token


def prepare(source_manifest,rows,output):
    source=json.loads(source_manifest.read_text())
    if source['project']!=PROJECT:raise ValueError('Unexpected source project')
    client=storage.Client(project=PROJECT,credentials=Credentials(token()))
    bucket=client.bucket(BUCKET)
    def derive(task):
        original=bucket.blob(task['object'],generation=int(task['generation']))
        with original.open('r',encoding='utf-8') as handle:
            reader=csv.DictReader(handle);selected=list(itertools.islice(reader,rows))
            fields=reader.fieldnames
        if len(selected)!=rows:raise ValueError('Source batch is too small')
        text=io.StringIO(newline='');writer=csv.DictWriter(text,fieldnames=fields)
        writer.writeheader();writer.writerows(selected);data=text.getvalue().encode()
        digest=hashlib.sha256(data).hexdigest()
        name=f"raw/citibike/benchmark/rows-{rows}/{task['generation']}-{Path(task['object']).name}"
        blob=bucket.blob(name)
        if blob.exists():
            blob.reload()
            if hashlib.sha256(blob.download_as_bytes()).hexdigest()!=digest:raise ValueError('Existing derived object differs')
        else:
            blob.metadata={'source_object':task['object'],'source_generation':task['generation'],'rows':str(rows),'sha256':digest}
            blob.upload_from_string(data,content_type='text/csv',if_generation_match=0)
        print('Prepared',name,rows,flush=True)
        result={'task_type':'trip_batch','city':'nyc','bucket':BUCKET,'object':name,'generation':str(blob.generation),'expected_rows':rows}
        provenance={'source_object':task['object'],'source_generation':task['generation'],'derived_object':name,'sha256':digest}
        return result,provenance,[row['ride_id'] for row in selected]
    with ThreadPoolExecutor(max_workers=4) as pool:derived=list(pool.map(derive,source['tasks']))
    ids=[ride for _,_,batch in derived for ride in batch]
    if len(set(ids))!=len(ids):raise ValueError('Benchmark source rows must be unique')
    result={'project':PROJECT,'created_at':datetime.now(timezone.utc).isoformat(),
            'derivation':f'First {rows} CSV records of each immutable original source batch; headers preserved',
            'source_manifest':str(source_manifest),'provenance':[p for _,p,_ in derived],'tasks':[t for t,_,_ in derived]}
    output.parent.mkdir(parents=True,exist_ok=True);output.write_text(json.dumps(result,indent=2))
    print('Unique source rows:',len(ids),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-manifest',type=Path,default=Path('experiments/results/worker-inputs.json'))
    parser.add_argument('--rows-per-batch',type=int,default=10000)
    parser.add_argument('--output',type=Path,default=Path('experiments/results/worker-inputs-10k.json'))
    args=parser.parse_args()
    if not 5000<=args.rows_per_batch<=50000:parser.error('Use 5,000-50,000 rows per batch')
    prepare(args.source_manifest,args.rows_per_batch,args.output)
