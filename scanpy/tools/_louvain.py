from typing import Optional, Tuple, Sequence, Type, Mapping, Any

import numpy as np
import pandas as pd
from anndata import AnnData
from natsort import natsorted
from scipy.sparse import spmatrix

from .. import utils
from .. import settings
from .. import logging as logg

try:
    from louvain.VertexPartition import MutableVertexPartition
except ImportError:
    class MutableVertexPartition: pass
    MutableVertexPartition.__module__ = 'louvain.VertexPartition'


def louvain(
    adata: AnnData,
    resolution: Optional[float] = None,
    random_state: int = 0,
    restrict_to: Optional[Tuple[str, Sequence[str]]] = None,
    key_added: Optional[str] = None,
    adjacency: Optional[spmatrix] = None,
    flavor: str = 'vtraag',
    directed: bool = True,
    use_weights: bool = False,
    partition_type: Optional[Type[MutableVertexPartition]] = None,
    partition_kwargs: Optional[Mapping[str, Any]]=None,
    copy: bool = False,
) -> Optional[AnnData]:
    """Cluster cells into subgroups [Blondel08]_ [Levine15]_ [Traag17]_.

    Cluster cells using the Louvain algorithm [Blondel08]_ in the implementation
    of [Traag17]_. The Louvain algorithm has been proposed for single-cell
    analysis by [Levine15]_.

    This requires to run :func:`~scanpy.api.pp.neighbors` first.

    Parameters
    ----------
    adata
        The annotated data matrix.
    resolution
        For the default flavor (``'vtraag'``), you can provide a resolution
        (higher resolution means finding more and smaller clusters),
        which defaults to 1.0. See “Time as a resolution parameter” in [Lambiotte09]_.
    random_state
        Change the initialization of the optimization.
    restrict_to
        Restrict the clustering to the categories within the key for sample
        annotation, tuple needs to contain ``(obs_key, list_of_categories)``.
    key_added
        Key under which to add the cluster labels. (default: ``'louvain'``)
    adjacency
        Sparse adjacency matrix of the graph, defaults to
        ``adata.uns['neighbors']['connectivities']``.
    flavor : {``'vtraag'``, ``'igraph'``}
        Choose between to packages for computing the clustering.
        ``'vtraag'`` is much more powerful, and the default.
    directed
        Interpret the ``adjacency`` matrix as directed graph?
    use_weights
        Use weights from knn graph.
    partition_type
        Type of partition to use.
        Only a valid argument if ``flavor`` is ``'vtraag'``.
    partition_kwargs
        Key word arguments to pass to partitioning,
        if ``vtraag`` method is being used.
    copy
        Copy adata or modify it inplace.

    Returns
    -------
    :obj:`None`
        By default (``copy=False``), updates ``adata`` with the following fields:

        ``adata.obs['louvain']`` (:class:`pandas.Series`, dtype ``category``)
            Array of dim (number of samples) that stores the subgroup id
            (``'0'``, ``'1'``, ...) for each cell.

    :class:`~anndata.AnnData`
        When ``copy=True`` is set, a copy of ``adata`` with those fields is returned.
    """
    logg.info('running Louvain clustering', r=True)
    if (flavor != 'vtraag') and (partition_type is not None):
        raise ValueError(
            '`partition_type` is only a valid argument when `flavour` is "vtraag"')
    adata = adata.copy() if copy else adata
    if adjacency is None and 'neighbors' not in adata.uns:
        raise ValueError(
            'You need to run `pp.neighbors` first to compute a neighborhood graph.')
    if adjacency is None:
        adjacency = adata.uns['neighbors']['connectivities']
    if restrict_to is not None:
        restrict_key, restrict_categories = restrict_to
        if not isinstance(restrict_categories[0], str):
            raise ValueError('You need to use strings to label categories, '
                             'e.g. \'1\' instead of 1.')
        for c in restrict_categories:
            if c not in adata.obs[restrict_key].cat.categories:
                raise ValueError(
                    '\'{}\' is not a valid category for \'{}\''
                    .format(c, restrict_key))
        restrict_indices = adata.obs[restrict_key].isin(restrict_categories).values
        adjacency = adjacency[restrict_indices, :]
        adjacency = adjacency[:, restrict_indices]
    if flavor in {'vtraag', 'igraph'}:
        if flavor == 'igraph' and resolution is not None:
            logg.warn('`resolution` parameter has no effect for flavor "igraph"')
        if directed and flavor == 'igraph':
            directed = False
        if not directed: logg.m('    using the undirected graph', v=4)
        g = utils.get_igraph_from_adjacency(adjacency, directed=directed)
        if use_weights:
            weights = np.array(g.es["weight"]).astype(np.float64)
        else:
            weights = None
        if flavor == 'vtraag':
            import louvain
            if partition_kwargs is None:
                partition_kwargs = {}
            if partition_type is None:
                partition_type = louvain.RBConfigurationVertexPartition
            if resolution is not None:
                partition_kwargs["resolution_parameter"] = resolution
            if use_weights:
                partition_kwargs["weights"] = weights
            logg.info('    using the "louvain" package of Traag (2017)')
            louvain.set_rng_seed(random_state)
            part = louvain.find_partition(
                g, partition_type,
                **partition_kwargs,
            )
            # adata.uns['louvain_quality'] = part.quality()
        else:
            part = g.community_multilevel(weights=weights)
        groups = np.array(part.membership)
    elif flavor == 'taynaud':
        # this is deprecated
        import networkx as nx
        import community
        g = nx.Graph(adjacency)
        partition = community.best_partition(g)
        groups = np.zeros(len(partition), dtype=int)
        for k, v in partition.items(): groups[k] = v
    else:
        raise ValueError('`flavor` needs to be "vtraag" or "igraph" or "taynaud".')
    unique_groups = np.unique(groups)
    n_clusters = len(unique_groups)
    if restrict_to is None:
        groups = groups.astype('U')
        key_added = 'louvain' if key_added is None else key_added
        adata.obs[key_added] = pd.Categorical(
            values=groups,
            categories=natsorted(unique_groups.astype('U')))
    else:
        key_added = restrict_key + '_R' if key_added is None else key_added
        all_groups = adata.obs[restrict_key].astype('U')
        prefix = '-'.join(restrict_categories) + ','
        new_groups = [prefix + g for g in groups.astype('U')]
        all_groups.iloc[restrict_indices] = new_groups
        adata.obs[key_added] = pd.Categorical(
            values=all_groups,
            categories=natsorted(all_groups.unique()))
    adata.uns['louvain'] = {}
    adata.uns['louvain']['params'] = {'resolution': resolution, 'random_state': random_state}
    logg.info('    finished', time=True, end=' ' if settings.verbosity > 2 else '\n')
    logg.hint('found {} clusters and added\n'
              '    \'{}\', the cluster labels (adata.obs, categorical)'
              .format(n_clusters, key_added))
    return adata if copy else None
