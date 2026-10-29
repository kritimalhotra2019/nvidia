args <- commandArgs(trailingOnly=TRUE)
suppressPackageStartupMessages(library(survey))
suppressPackageStartupMessages(library(jsonlite))
job <- fromJSON(args[1])
dat <- read.csv(job$path, check.names=FALSE)
options(survey.lonely.psu="fail")
form <- function(x) as.formula(paste("~",x))
ids <- if (is.null(job$cluster)) ~1 else form(job$cluster)
strata <- if (is.null(job$strata)) NULL else form(job$strata)
design <- svydesign(ids=ids, strata=strata, weights=form(job$weight_column), data=dat, nest=TRUE)
results <- list()
for (column in job$columns) {
    if (is.null(job$group_by)) {
        estimate <- svymean(form(column),design,na.rm=TRUE)
        ci <- confint(estimate)
        results[[column]] <- list(mean=unname(coef(estimate)[1]),se=unname(SE(estimate)[1]),
            ci_low=unname(ci[1,1]),ci_high=unname(ci[1,2]),valid_n=sum(!is.na(dat[[column]])))
    } else {
        estimate <- svyby(form(column),form(job$group_by),design,svymean,na.rm=TRUE,vartype=c("se","ci"))
        results[[column]] <- as.data.frame(estimate)
    }
}
cat(toJSON(list(results=results,rows=nrow(dat),design=list(ids=job$cluster,strata=job$strata,weights=job$weight_column)),auto_unbox=TRUE,na="null",digits=16))
