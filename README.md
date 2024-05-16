Download the code:
```
git clone https://github.com/dmlpt/Strong-DocFVLM
cd Strong-DocFVLM
```


Follow the instructions at https://github.com/X-PLUG/mPLUG-DocOwl/tree/main/DocOwl1.5 to install mPLUG-DocOwl1.5


Create a symbolic link for the data inside Strong-DocFVLM folder
```
ln -s /path/to/challenge/data data
```

Download models [https://strong-docfvlm.s3.us-east-2.amazonaws.com/checkpoint.tar] from aws and extract it inside Strong-DocFVLM folder : i.e 
```
tar -xvf checkpoint.tar (Inside Strong-DocFVLM folder)
```


Inference on 4 datasets:
```
CUDA_VISIBLE_DEVICES=0 python eval_mplug_owl.py --output_path results/output_4_datasets.json --data_path data/processed_data  --test_file_name converted_output_test.json --sub_ds_list docvqa,infographicvqa,websrc,wtq
```



Inference on remaining 6 datasets:
```
CUDA_VISIBLE_DEVICES=4 python eval_mplug_owl.py --output_path results/output_6_datasets.json --data_path data/processed_data --test_file_name converted_output_test.json --sub_ds_list iconqa_fill_in_blank,funsd,iconqa_choose_txt,wildreceipt,textbookqa,tabfact
```
