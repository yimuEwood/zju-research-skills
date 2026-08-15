# Omics executor routing

| Design | Preferred executor | Required handoff |
|---|---|---|
| Two-group unpaired bulk counts | DESeq2 or edgeR | raw counts, sample sheet, design formula, contrast |
| Paired or blocked bulk counts | DESeq2/edgeR/limma with explicit block | subject or block ID and contrast |
| Repeated measures | dream/variancePartition or justified mixed model | subject ID, time, covariance decision |
| Single-cell RNA-seq | Scanpy/Seurat plus pseudobulk where inference is required | cell QC, sample IDs, batch, annotation evidence |
| Proteomics intensity | limma/MSstats or assay-specific workflow | transformation, missingness and normalization plan |
| Metabolomics | assay-specific preprocessing plus multivariable audit | feature identity confidence and batch/QC samples |

Never select an executor solely because it is installed. Match it to the experimental unit and data-generating process.
