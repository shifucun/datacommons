# Copyright 2017 Google Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
""" DataCommons base public API.

Contains Client which connects to the DataCommons knowledge graph, DCNode which
wraps a node in the graph, and DCFrame which provides a tabular view of graph
data.
"""

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

from collections import defaultdict, OrderedDict
from . import utils
import json
import requests
import pandas as pd

# REST API endpoint root
_API_ROOT = "http://mixergrpc.endpoints.datcom-mixer.cloud.goog"

# REST API endpoint paths
_API_ENDPOINTS = {
  "get_node": "/node",
  "get_property": "/property",
  "get_property_value": "/propertyvalue",
  "get_triples": "/triples"
}

# Database paths
_BIG_QUERY_PATH = 'google.com:datcom-store-dev.dc_v3_clustered'

# The default value to limit to
_MAX_LIMIT = 100


class DCNode(object):
  """ Wraps a node found in the DataCommons knowledge graph. Supports the
  following functionalities.

  - Querying for properties that have this node as either a subject or object.
  - Querying for values in triples containing this node and a given property.
  - Querying for all triples containing this node.
  """

  def __init__(self, **kwargs):
    """ Constructor for the node.

    DCNode fields:
      _dcid: The dcid of the node. This should never change after the creation
        of the DCNode.
      _name: The name of the node
      _value: A node is a leaf node if it only contains a value. Leaf nodes do
        not have a specific dcid assigned to them.
      _types: A list of types associated with the node
      _in_props: A map from incoming property to other nodes.
      _out_props: A map from outgoing property to other nodes.

    Raises:
      ValueError: If neither of dcid or value are provided.
    """
    # TODO(antaresc): Remove this after id -> dcid in the EntityInfo proto
    if 'id' in kwargs and 'dcid' not in kwargs:
      kwargs['dcid'] = kwargs['id']

    # TODO(antaresc): Make the return value not have a key if it maps to an
    #   empty string.
    if ('dcid' not in kwargs or not bool(kwargs['dcid'])) and\
      ('value' not in kwargs or not bool(kwargs['value'])):
      raise ValueError('Must specify one of "dcid" or "value"')

    # Initialize all fields
    self._dcid = None
    self._name = None
    self._value = None
    self._types = []
    self._in_props = {}
    self._out_props = {}

    # Populate fields based on if this is a node with a dcid or a leaf node.
    if 'dcid' in kwargs and bool(kwargs['dcid']):
      if 'name' in kwargs and 'types' in kwargs:
        self._name = kwargs['name']
        self._types = kwargs['types']
      else:
        # Send a request to get basic node information from the graph.
        params = "?dcid={}".format(kwargs['dcid'])
        url = _API_ROOT + _API_ENDPOINTS['get_node'] + params
        res = requests.get(url)
        payload = utils.format_response(res)

        # Set the name and type
        if 'name' in kwargs:
          self._name = kwargs['name']
        elif 'name' in payload:
          self._name = payload['name']
        if 'types' in kwargs:
          self._types = kwargs['types']
        elif 'types' in payload:
          self._types = payload['types']
      # Set the dcid
      self._dcid = kwargs['dcid']
    else:
      self._value = kwargs['value']

  def __eq__(self, other):
    """ Overrides == operator.

    Two nodes are equal if and only if they have the same dcid. Leaf-nodes are
    by definition not equal to each other. This means a comparison between a
    leaf node and a node with a dcid or two leaf nodes is always False.
    """
    return self._dcid and other._dcid and self._dcid == other._dcid

  def __ne__(self, other):
    """ Overrides != operator. """
    return not (self == other)

  def __str__(self):
    """ Overrides str() operator. """
    fields = {}
    if self._dcid:
      fields['dcid'] = self._dcid
    if self._name:
      fields['name'] = self._name
    if self._value:
      fields['value'] = self._value
    return str(fields)

  def __hash__(self):
    """ Overrides hash() operator.

    The hash of a node with a dcid is the hash of the string "dcid: <the dcid>"
    while the hash of a leaf is the hash of "value: <the value>".
    """
    if self.is_leaf():
      return hash('value:{}'.format(self._value))
    return hash('dcid:{}'.format(self._dcid))

  def is_leaf(self):
    """ Returns true if the node only contains a single value. """
    return bool(self._value)

  def get_properties(self, outgoing=True):
    """ Returns a list of properties associated with this node.

    Args:
      outgoing: whether or not the node is a subject or object.
    """
    pass

  def get_property_values(self,
                          prop,
                          outgoing=True,
                          value_type=None,
                          reload=False,
                          limit=_MAX_LIMIT):
    """ Returns a list of values mapped to this node by a given prop.

    Args:
      prop: The property adjacent to the current node.
      outgoing: whether or not the node is a subject or object.
      value_type: Filter values mapped to this node by the given type.
      reload: Send the query through cache.
      limit: The maximum number of values to return.
    """
    # Check if there are enough property values in the cache.
    if outgoing and prop in self._out_props:
      if len(self._out_props[prop]) >= limit:
        return self._out_props[prop][:limit]
      limit = limit - len(self._out_props[prop])
    elif not outgoing and prop in self._in_props:
      if len(self._in_props[prop]) >= limit:
        return self._in_props[prop][:limit]
      limit = limit - len(self._in_props[prop])

    # Query for the rest of the nodes to meet limit. First create request body
    req_json = {
      "dcid": [self._dcid],
      "property": prop,
      "outgoing": outgoing,
      "reload": reload,
      "limit": limit
    }
    if value_type:
      req_json["value_type"] = value_type

    # Send the request to GetPropertyValue
    url = _API_ROOT + _API_ENDPOINTS['get_property_value']
    res = requests.post(url, json=req_json)
    payload = utils.format_response(res)

    # Create nodes for each property value returned.
    prop_vals = set()
    if self._dcid in payload and prop in payload[self._dcid]:
      nodes = payload[self._dcid][prop]
      for node in nodes:
        prop_vals.add(DCNode(**node))

    # Cache the results and set prop_vals to the appropriate list of nodes.
    if outgoing:
      if prop not in self._out_props:
        self._out_props[prop] = []
      self._out_props[prop] = list(set(self._out_props[prop]).union(prop_vals))
      prop_vals = self._out_props[prop][:limit]
    else:
      if prop not in self._in_props:
        self._in_props[prop] = []
      self._in_props[prop] = list(set(self._in_props[prop]).union(prop_vals))
      prop_vals = self._in_props[prop][:limit]

    # Return the results
    return prop_vals

  def get_triples(self):
    """ Returns a list of triples where this node is either a subject or object.

    Args:
      outgoing: whether or not the node is a subject or object.
    """
    pass

# class DCFrame(object):
#   """ Provides a tabular view of the DataCommons knowledge graph. """
#
#   def __init__(self,
#                file_name=None,
#                datalog_query=None,
#                labels=None,
#                select=None,
#                process=None,
#                type_hint=None,
#                rows=100,
#                db_path=None,
#                client_id=_SANDBOX_CLIENT_ID,
#                client_secret=_SANDBOX_CLIENT_SECRET,
#                api_root=_SANDBOX_API_ROOT):
#     """ Initializes the DCFrame.
#
#     A DCFrame can also be initialized by providing the file name of a cached
#     frame or a datalog query. When a datalog query is provided, the results
#     of the query are stored in the frame with selected variables set as the
#     column names. Additional fields such as labels, select, process, etc. can
#     be provided to manipuate the results of the datalog query before it is
#     wrapped by the DCFrame.
#
#     The DCFrame requires typing information for the columns that it maintains.
#     If the frame is initialized from a query then either the query variable
#     types must be inferrable from the query, or it must be provided in the type
#     hint.
#
#     Args:
#       file_name: File name of a cached DCTable.
#       datalog_query: Query object representing datalog query [TODO(shanth): link]
#       labels: A map from the query variables to column names in the DCFrame.
#       select: A function that takes in a row and returns true if the row in the
#         result should be added to the final DCFrame. Functions should index into
#         columns using column names prior to relabeling.
#       process: A function that takes in a Pandas DataFrame. Can be used for
#         post processing the results such as converting columns to certain types.
#         Functions should index into columns using names prior to relabeling.
#       type_hint: A map from column names to the type that the column contains.
#       db_path: The path for the database to query.
#       client_id: The API client id
#       client_secret: The API client secret
#       api_root: The API root url
#
#     Raises:
#       RuntimeError: some problem with executing query (hint in the string)
#     """
#     self._client = Client(db_path=db_path,
#                           client_id=client_id,
#                           client_secret=client_secret,
#                           api_root=api_root)
#     self._dataframe = pd.DataFrame()
#     self._col_types = {}
#
#     # Read the dataframe from cache if a file name is provided or initialize
#     # from a datalog query if the query is provided
#     if file_name:
#       try:
#         response = self._client._service.read_dataframe(
#             file_name=file_name
#         ).execute()
#       except Exception as e:  # pylint: disable=broad-except
#         raise RuntimeError('Failed to read "{}": {}'.format(file_name, e))
#
#       # Inflate the json string.
#       data = json.loads(response['data'])
#       self._dataframe = pd.read_json(data['dataframe'])
#       self._col_types = data['col_types']
#     elif datalog_query:
#       variables = datalog_query.variables()
#       var_types = datalog_query.var_types()
#       query_string = str(datalog_query)
#       pd_frame = self._client.query(query_string, rows=rows)
#       pd_frame = pd_frame.dropna()
#
#       # If variable type is not provided in type_hint or from the query, infer
#       # the type as text.
#       for var in variables:
#         if var not in var_types and (type_hint is None or var not in type_hint):
#           var_types[var] = 'Text'
#
#       # Processing is run the order of row filtering via select, table
#       # manipulation via process, and column renaming via labels,
#       if select:
#         pd_frame = pd_frame[pd_frame.apply(select, axis=1)]
#       if process:
#         pd_frame = process(pd_frame)
#       for col in pd_frame:
#         # Set the column types and remap if the column labels are provided. Only
#         # add types for columns that appear in the dataframe. This is critical
#         # as "process" may delete columns from the query result.
#         col_name = col
#         if labels and col in labels:
#           col_name = labels[col]
#         if type_hint and col in type_hint:
#           self._col_types[col_name] = type_hint[col]
#         else:
#           self._col_types[col_name] = var_types[col]
#       if labels:
#         pd_frame = pd_frame.rename(index=str, columns=labels)
#       self._dataframe = pd_frame.reset_index(drop=True)
#
#   def columns(self):
#     """ Returns the set of column names for this frame.
#
#     Returns:
#       Set of column names for this frame.
#     """
#     return [col for col in self._dataframe]
#
#   def types(self):
#     """ Returns a map from column name to associated DataCommons type.
#
#     Returns:
#       Map from column name to column type.
#     """
#     return self._col_types
#
#   def pandas(self, col_names=None, ignore_populations=False):
#     """ Returns a copy of the data in this view as a Pandas DataFrame.
#
#     Args:
#       col_names: An optional list specifying which columns to extract.
#       ignore_populations: Ignores all columns that have type
#         StatisticalPopulation. col_names takes precedence over this argument
#
#     Returns: A deep copy of the underlying Pandas DataFrame.
#     """
#     if not col_names:
#       col_names = list(self._dataframe)
#     if ignore_populations:
#       col_names = list(filter(lambda name: self._col_types[name] != 'StatisticalPopulation', col_names))
#     return self._dataframe[col_names].copy()
#
#   def csv(self, col_names=None):
#     """ Returns the data in this view as a CSV string.
#
#     Args:
#       col_names: An optional list specifying which columns to extract.
#
#     Returns:
#       The DataFrame exported as a CSV string.
#     """
#     if col_names:
#       return self._dataframe[col_names].to_csv(index=False)
#     return self._dataframe.to_csv(index=False)
#
#   def tsv(self, col_names=None):
#     """ Returns the data in this view as a TSV string.
#
#     Args:
#       col_names: An optional list specifying which columns to extract.
#
#     Returns:
#       The DataFrame exported as a TSV string.
#     """
#     if col_names:
#       return self._dataframe[col_names].to_csv(index=False, sep='\t')
#     return self._dataframe.to_csv(index=False, sep='\t')
#
#   def rename(self, labels):
#     """ Renames the columns of the DCFrame.
#
#     Args:
#       labels: A map from current to new column names.
#     """
#     col_types = {}
#     for col in self._dataframe:
#       col_name = col
#       if col in labels:
#         col_name = labels[col]
#       col_types[col_name] = self._col_types[col]
#     self._col_types = col_types
#     self._dataframe = self._dataframe.rename(index=str, columns=labels)
#
#   def add_column(self, col_name, col_type, col_vals):
#     """ Adds a column containing the given values of the given type.
#
#     Args:
#       col_name: The name of the column
#       col_type: The type of the column
#       col_vals: The values in the given column
#     """
#     self._col_types[col_name] = col_type
#     self._dataframe[col_name] = col_vals
#
#   def expand(self, property, seed_col_name, new_col_name, new_col_type=None, outgoing=True, rows=100):
#     """ Creates a new column containing values for the given property.
#
#     For each entity in the given seed column, queries for entities related to
#     the seed entity via the given property. Results are stored in a new column
#     under the provided name. The seed column should contain only DCIDs.
#
#     Args:
#       property: The property to add to the table.
#       seed_col_name: The column name that contains dcids that the added
#         properties belong to.
#       new_col_name: The new column name.
#       new_col_type: The type contained by the new column. Provide this if the
#         type is not immediately inferrable.
#       outgoing: Set this flag if the seed property points away from the entities
#         denoted by the seed column. That is the seed column serve as subjects
#         in triples formed with the given property.
#       rows: The maximum number of rows returned by the query results.
#
#     Raises:
#       ValueError: when input argument is not valid.
#     """
#     if seed_col_name not in self._dataframe:
#       raise ValueError(
#           'Expand error: {} is not a valid seed column.'.format(seed_col_name))
#     if new_col_name in self._dataframe:
#       raise ValueError(
#           'Expand error: {} is already a column.'.format(new_col_name))
#
#     # Get the seed column information
#     seed_col = self._dataframe[seed_col_name]
#     seed_col_type = self._col_types[seed_col_name]
#     if seed_col_type == 'Text':
#       raise ValueError(
#           'Expand error: {} must contain DCIDs'.format(seed_col_name))
#
#     # Determine the new column type
#     if new_col_type is None:
#       new_col_type = self._client.property_type(seed_col_type, property, outgoing=outgoing)
#     if new_col_type is None and outgoing:
#       new_col_type = 'Text'
#     elif new_col_type is None:
#       raise ValueError(
#           'Expand error: {} does not have incoming property {}'.format(seed_col_type, property))
#
#     # Get the list of DCIDs to query for
#     dcids = list(seed_col)
#     if not dcids:
#       # All entries in the seed column are empty strings. The new column should
#       # contain no entries.
#       self._dataframe[new_col_name] = ''
#       self._col_types[new_col_name] = new_col_type
#       return
#
#     # Construct the query
#     seed_col_var = '?' + seed_col_name.replace(' ', '_')
#     new_col_var = '?' + new_col_name.replace(' ', '_')
#     labels = {seed_col_var: seed_col_name, new_col_var: new_col_name}
#     type_hint = {seed_col_var: seed_col_type, new_col_var: new_col_type}
#
#     query = utils.DatalogQuery()
#     query.add_variable(seed_col_var, new_col_var)
#     query.add_constraint('?node', 'typeOf', seed_col_type)
#     query.add_constraint('?node', 'dcid', dcids)
#     query.add_constraint('?node', 'dcid', seed_col_var)
#     if outgoing:
#       query.add_constraint('?node', property, new_col_var)
#     else:
#       query.add_constraint(new_col_var, property, '?node')
#
#     # Create a new DCFrame and merge it in
#     new_frame = DCFrame(datalog_query=query, rows=rows, labels=labels, type_hint=type_hint)
#     self.merge(new_frame)
#
#   def merge(self, frame, how='left', default=''):
#     """ Joins the given frame into the current frame along shared column names.
#
#     Args:
#       frame: The DCFrame to merge in.
#       how: Optional argument specifying the joins type to perform. Valid types
#         include 'left', 'right', 'inner', and 'outer'
#       default: The default place holder for an empty cell produced by the join.
#
#     Raises:
#       ValueError: if the given arguments are not valid. This may include either
#         the given or current DCFrame does not contain the columns specified.
#     """
#     merge_on = set(self.columns()) & set(frame.columns())
#     merge_on = list(merge_on)
#
#     # If the current dataframe is empty, select the given dataframe. If the
#     # tables have no columns in common, perform a cross join. Otherwise join on
#     # common columns.
#     if self._dataframe.empty:
#       self._col_types = {}
#       self._dataframe = frame._dataframe
#     elif len(merge_on) == 0:
#       # Construct a unique dummy column name
#       cross_on = ''.join(self.columns() + frame.columns())
#
#       # Perform the cross join
#       curr_frame = self._dataframe.assign(**{cross_on: 1})
#       new_frame = frame._dataframe.assign(**{cross_on: 1})
#       merged = curr_frame.merge(new_frame)
#       self._dataframe = merged.drop(cross_on, 1)
#     else:
#       # Verify that columns being merged have the same type
#       for col in merge_on:
#         if self._col_types[col] != frame._col_types[col]:
#           raise ValueError(
#               'Merge error: columns type mismatch for {}.\n  Current: {}\n  Given: {}'.format(col, self._col_types[col], frame._col_types[col]))
#
#       # Merge dataframe, column types, and property maps
#       self._dataframe = self._dataframe.merge(frame._dataframe, how=how, left_on=merge_on, right_on=merge_on)
#       self._dataframe = self._dataframe.fillna(default)
#
#     # Merge the types
#     self._col_types.update(frame._col_types)
#
#   def clear(self):
#     """ Clears all the data stored in this extension. """
#     self._col_types = {}
#     self._dataframe = pd.DataFrame()
#
#   def save(self, file_name):
#     """ Saves the current DCFrame to the DataCommons cache with given file name.
#
#     Args:
#       file_name: The name used to store the current DCFrame.
#
#     Returns:
#       The file name that the
#
#     Raises:
#       RuntimeError: when failed to save the dataframe.
#     """
#     assert self._client._inited, 'Initialization was unsuccessful, cannot execute Query'
#
#     # Saves the DCFrame to cache
#     data = json.dumps({
#       'dataframe': self._dataframe.to_json(),
#       'col_types': self._col_types
#     })
#     try:
#       response = self._client._service.save_dataframe(body={
#           'data': data,
#           'file_name': file_name
#       }).execute()
#     except Exception as e:  # pylint: disable=broad-except
#       raise RuntimeError('Failed to save dataframe: {}'.format(e))
#     return response['file_name']
