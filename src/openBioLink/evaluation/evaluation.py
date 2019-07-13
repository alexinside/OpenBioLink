import numpy
import pandas

import evaluation.evalConfig as evalConst
import evaluation.evaluationIO as io
import globalConfig as globConst
import utils
from .metricTypes import RankMetricType, ThresholdMetricType
from .models.model import Model


class Evaluation:
    def __init__(self, model: Model, training_set_path=None, test_set_path=None):
        self.model = model
        if training_set_path:
            self.training_examples = pandas.read_csv(training_set_path, sep="\t",
                                                     names=globConst.COL_NAMES_SAMPLES)
        else:
            self.training_examples = None
        if test_set_path:
            self.test_examples = pandas.read_csv(test_set_path, sep="\t",
                                       names=globConst.COL_NAMES_SAMPLES)


    def train(self):
        self.model.train(self.training_examples)


    def evaluate(self,
                 metrics: list,
                 ks=None,
                 corrupted_triples_folder=None,
                 nodes_path=None,
                 ):
        if not ks:
            ks=evalConst.DEFAULT_HITS_AT_K 

        ranked_metrics = [m for m in RankMetricType]
        threshold_metrics = [m for m in ThresholdMetricType]
        metrics_results = {}

        if len([x for x in ranked_metrics if x in metrics])>0:
            ranked_metrics_results = self.evaluate_ranked_metrics(metrics=metrics,
                                         ks=ks,
                                         corrupted_triples_folder=corrupted_triples_folder,
                                         nodes_path=nodes_path)
            metrics_results.update(ranked_metrics_results)

        if len([x for x in threshold_metrics if x in metrics]) > 0:
            threshold_metrics_results = self.evaluate_threshold_metrics(metrics=metrics)
            metrics_results.update(threshold_metrics_results)

        io.write_metric_results(metrics_results)

        return metrics_results


    def evaluate_ranked_metrics(self, ks, metrics, corrupted_triples_folder=None, nodes_path=None):
        metric_results = {}
        # get corrupted triples
        pos_test_examples = self.test_examples[self.test_examples[globConst.VALUE_COL_NAME] == 1]
        if not corrupted_triples_folder:
            nodes = pandas.read_csv(nodes_path, sep="\t", names=globConst.COL_NAMES_NODES)
            corrupted_head_dict, corrupted_tail_dict = utils.calc_corrupted_triples(
                pos_examples=pos_test_examples[[globConst.NODE1_ID_COL_NAME, 
                                                globConst.EDGE_TYPE_COL_NAME, 
                                                globConst.NODE2_ID_COL_NAME]],
                nodes=nodes)
        else:
            #testme
            corrupted_head_dict, corrupted_tail_dict = io.read_corrupted_triples(corrupted_triples_folder)

        num_examples = len(corrupted_head_dict.keys())
        ranks_corrupted_heads, unfiltered_ranks_corrupted_heads = self.get_filtered_and_unfiltered_ranks(corrupted_head_dict)
        ranks_corrupted_tails, unfiltered_ranks_corrupted_tails = self.get_filtered_and_unfiltered_ranks(corrupted_tail_dict)

        # HITS@K
        if RankMetricType.HITS_AT_K in metrics:
            metric_results[RankMetricType.HITS_AT_K] = self.calculate_hits_at_k(ks= ks,
                                                                                ranks_corrupted_heads=ranks_corrupted_heads,
                                                                                ranks_corrupted_tails=ranks_corrupted_tails,
                                                                                num_examples=num_examples)
        # HITS@K unfiltered
        if RankMetricType.HITS_AT_K_UNFILTERED in metrics:
            metric_results[RankMetricType.HITS_AT_K_UNFILTERED] = self.calculate_hits_at_k(ks= ks,
                                                                                ranks_corrupted_heads=unfiltered_ranks_corrupted_heads,
                                                                                ranks_corrupted_tails=unfiltered_ranks_corrupted_tails,
                                                                                num_examples=num_examples)
        # MRR
        if RankMetricType.MRR in metrics:
            metric_results[RankMetricType.MRR] = self.calculate_mrr(ranks_corrupted_heads=ranks_corrupted_heads,
                                                                    ranks_corrupted_tails=ranks_corrupted_tails,
                                                                    num_examples=num_examples)
        # MRR unfiltered
        if RankMetricType.MRR_UNFILTERED in metrics:
            metric_results[RankMetricType.MRR] = self.calculate_mrr(ranks_corrupted_heads=unfiltered_ranks_corrupted_heads,
                                                                    ranks_corrupted_tails=unfiltered_ranks_corrupted_tails,
                                                                    num_examples=num_examples)
        return metric_results


    def evaluate_threshold_metrics(self, metrics):
        metric_results={}
        ranked_test_examples, sorted_indices = self.model.get_ranked_predictions(self.test_examples)
        ranked_scores = ranked_test_examples[globConst.SCORE_COL_NAME]
        ranked_labels = self.test_examples[globConst.VALUE_COL_NAME][sorted_indices]
        # ROC Curve
        if ThresholdMetricType.ROC in metrics:
            fpr, tpr = self.calculate_roc_curve(labels=ranked_labels, scores=ranked_scores)
            metric_results[ThresholdMetricType.ROC] = (fpr, tpr)
        # Precision Recall Curve
        if ThresholdMetricType.PR_REC_CURVE in metrics:
            pr, rec = self.calculate_pr_curve(ranked_labels, ranked_scores)
            metric_results[ThresholdMetricType.PR_REC_CURVE] = (pr, rec)
        # ROC AUC
        if ThresholdMetricType.ROC_AUC:
            if ThresholdMetricType.ROC in metric_results.keys():
                fpr, tpr = metric_results[ThresholdMetricType.ROC]
            else:
                fpr, tpr = self.calculate_roc_curve(labels=ranked_labels, scores=ranked_scores)
                #todo ? auchunique?
            roc_auc = self.calculate_auc(fpr, tpr)
            metric_results[ThresholdMetricType.ROC_AUC] = roc_auc
        # Precision Recall AUC
        if ThresholdMetricType.PR_AUC:
            if ThresholdMetricType.PR_AUC in metric_results.keys():
                pr, rec = metric_results[ThresholdMetricType.PR_REC_CURVE]
            else:
                pr, rec = self.calculate_pr_curve(labels=ranked_labels, scores=ranked_scores)
                pr = numpy.asarray(pr)
                rec = numpy.asarray(rec)
            _, indices = numpy.unique(pr, return_index=True)
            pr_unique = pr[indices]
            rec_unique = rec[indices]
            pr_auc = self.calculate_auc(pr_unique, rec_unique)
            metric_results[ThresholdMetricType.PR_AUC] = pr_auc
        return metric_results


    def get_filtered_and_unfiltered_ranks(self, corrupted_dict, filtered=True, unfiltered=True):
        filtered_ranks=[]
        unfiltered_ranks=[]
        for true_triple, corrupted_df in corrupted_dict.items():
            corrupted_df.loc[len(corrupted_df)] = list(true_triple) + [1]
            ranked_examples, sorted_indices = self.model.get_ranked_predictions(corrupted_df)
            #testme
            #ranked_examples.reset_index(drop=True, inplace=True)
            ranked_labels = corrupted_df[globConst.VALUE_COL_NAME][sorted_indices]
            if filtered:
                filtered_ranks.append(self.get_rank_of_triple(true_triple, ranked_examples))
            if unfiltered:
                unfiltered_ranks.append(self.get_first_positive_rank(ranked_labels))
        return filtered_ranks, unfiltered_ranks


    def get_rank_of_triple (self, triple_value, ranked_predictions):
        head, relation, tail = triple_value
        index = ranked_predictions.index[(ranked_predictions[globConst.NODE1_ID_COL_NAME] == head)
                                 & (ranked_predictions[globConst.EDGE_TYPE_COL_NAME]==relation)
                                 & (ranked_predictions[globConst.NODE2_ID_COL_NAME]==tail)].tolist()[0] #todo check for multiple returns
        return index+1


    def get_first_positive_rank (self, labels):
        rank = next(x for x in labels if x==1)
        return rank


    def calculate_hits_at_k(self, ks, ranks_corrupted_heads, ranks_corrupted_tails, num_examples):
        corrupted_heads_hits_at_k = dict()
        corrupted_tails_hits_at_k = dict()
        for k in ks:
            corrupted_heads_hits_at_k[k] = len([x for x in ranks_corrupted_heads if x <= k]) / num_examples
            corrupted_tails_hits_at_k[k] = len([x for x in ranks_corrupted_tails if x <= k]) / num_examples
        return corrupted_heads_hits_at_k, corrupted_tails_hits_at_k


    def calculate_mrr(self,ranks_corrupted_heads, ranks_corrupted_tails, num_examples):
        inverse_ranks_corrupted_heads = [1 / n for n in ranks_corrupted_heads]
        inverse_ranks_corrupted_tails = [1 / n for n in ranks_corrupted_tails]
        mrr_heads = sum(inverse_ranks_corrupted_heads) / num_examples
        mrr_tails = sum(inverse_ranks_corrupted_tails) / num_examples
        return mrr_heads, mrr_tails


    def calculate_roc_curve(self, labels, scores ):
        from sklearn.metrics import roc_curve
        fpr, tpr, _ = roc_curve(labels.values, scores.values)
        return  list(fpr), list(tpr)


    def calculate_pr_curve(self, labels, scores ):
        from sklearn.metrics import  precision_recall_curve
        precision, recall, thresholds = precision_recall_curve(labels, scores)
        return  list(precision), list(recall)


    def calculate_auc(self, x_values, y_values):
        from sklearn.metrics import auc
        auc_value = auc(x_values, y_values)
        return auc_value
