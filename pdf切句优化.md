PDF 切句（代码已改，生产索引未切换）
文件：arag-main/retrieval_adaptor/pdf_ingest.py、arag-main/scripts/build_index.py

已实现：

剥离粘在引言前的刊名/作者（peel_glued_front_matter）
英文分句保护 Fig. 1 / et al.（split_english_sentences）
跨 chunk 半句拼接（repair_cross_chunk_sentences）
图注续行跳过、机构/刊头句不进检索库（is_indexable_sentence）
ingest_papers 可接受单个 PDF 路径（避免 P001+P002+P003 拼成一套 id）
--skip-embed 只切句写句表；--sentences-out 导出 CSV