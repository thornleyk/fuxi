#!/usr/bin/env python
from pprint import pprint
from FuXi.Rete.Proof import GenerateProof
from FuXi.Rete import ReteNetwork
from FuXi.Rete.AlphaNode import SUBJECT,PREDICATE,OBJECT,VARIABLE
from FuXi.Rete.BetaNode import PartialInstanciation, LEFT_MEMORY, RIGHT_MEMORY
from FuXi.Rete.RuleStore import N3RuleStore, SetupRuleStore
from FuXi.Rete.Util import renderNetwork,generateTokenSet, xcombine
from FuXi.DLP.DLNormalization import NormalFormReduction
from FuXi.LP.BackwardFixpointProcedure import BackwardFixpointProcedure
from FuXi.LP import IdentifyHybridPredicates
from FuXi.SPARQL.BackwardChainingStore import * 
from FuXi.DLP import MapDLPtoNetwork, DisjunctiveNormalForm
from FuXi.Horn import *
from FuXi.Horn.HornRules import HornFromN3, Ruleset
from FuXi.Syntax.InfixOWL import *
from FuXi.Rete.TopDown import *
from FuXi.Rete.Proof import ProofBuilder, PML, GMP_NS
from FuXi.Rete.Magic import *
from FuXi.Rete.SidewaysInformationPassing import *
from rdflib.sparql.bison.Query import Prolog
from rdflib.Namespace import Namespace
from rdflib import plugin,RDF,RDFS,URIRef,URIRef,Literal,Variable
from rdflib.store import Store
from cStringIO import StringIO
from rdflib.Graph import Graph,ReadOnlyGraphAggregate,ConjunctiveGraph
from rdflib.syntax.NamespaceManager import NamespaceManager
from rdflib.sparql.parser import parse as ParseSPARQL
from rdflib.sparql.Algebra import ReduceGraphPattern
import unittest, time, warnings,sys

TEMPLATES = Namespace('http://code.google.com/p/fuxi/wiki/BuiltinSPARQLTemplates#')

RDF_SERIALIZATION_FORMATS = [
    'xml', 
    'TriX', 
    'pretty-xml',
    'turtle',
    'n3', 
]

def main():
    from optparse import OptionParser
    op = OptionParser('usage: %prog [options] factFile1 factFile2 ... factFileN')
    op.add_option('--why', 
                  default=None,
      help = 'Specifies the goals to solve for')
    op.add_option('--closure', 
                  action='store_true',
                  default=False,
      help = 'Whether or not to serialize the inferred triples'+ 
             ' along with the original triples.  Otherwise '+
              '(the default behavior), serialize only the inferred triples')
    op.add_option('--naive',
                  action='store_true',
                  default=False,
      help = 'Naively perform forward chaining over rules and facts using the '+
             'RETE network')
    op.add_option('--imports', 
                action='store_true',
                default=False,
    help = 'Whether or not to follow owl:imports in the fact graph')
    op.add_option('--output', 
                  default='n3',
                  metavar='RDF_FORMAT',
                  choices = ['xml', 
                             'TriX', 
                             'n3', 
                             'pml',
                             'proof-graph',
                             'nt',
                             'rif',
                             'rif-xml',
                             'conflict',
                             'man-owl'],
      help = "Serialize the inferred triples and/or original RDF triples to STDOUT "+
             "using the specified RDF syntax ('xml','pretty-xml','nt','turtle', "+
             "or 'n3') or to print a summary of the conflict set (from the RETE "+
             "network) if the value of this option is 'conflict'.  If the the "+
             " value is 'rif' or 'rif-xml', Then the rules used for inference "+
             "will be serialized as RIF.  If the value is 'pml' and --why is used, "+
             " then the PML RDF statements are serialized.  If output is "+
             "'proof-graph then a graphviz .dot file of the proof graph is printed. "+
             "Finally if the value is 'man-owl', then the RDF facts are assumed "+
             "to be OWL/RDF and serialized via Manchester OWL syntax. The default is %default")
    op.add_option('--class',
                  dest='classes',
                  action='append',
                  default=[],
                  metavar='QNAME', 
      help = 'Used with --output=man-owl to determine which '+
             'classes within the entire OWL/RDF are targetted for serialization'+
             '.  Can be used more than once')
    op.add_option('--hybrid',
                  action='store_true',
                  default=False,
      help = 'Used to determine whether or not to '+
             'peek into the fact graph to identify predicates that are both '+
             'derived and base.  This is expensive for large fact graphs'+
             'and is explicitely not used against SPARQL endpoints')
    op.add_option('--property',
                  action='append',
                  dest='properties',
                  default=[],
                  metavar='QNAME', 
      help = 'Used with --output=man-owl or --extract to determine which '+
             'properties are serialized / extracted.  Can be used more than once')
    op.add_option('--normalize', 
                  action='store_true',
                  default=False,
      help = "Used with --output=man-owl to attempt to determine if the ontology is 'normalized' [Rector, A. 2003]"+
      "The default is %default")
    op.add_option('--ddlGraph', 
                default=False,
      help = "The location of a N3 Data Description document describing the IDB predicates")      
    op.add_option('--input-format', 
                  default='xml',
                  dest='inputFormat',
                  metavar='RDF_FORMAT',
                  choices = ['xml', 'trix', 'n3', 'nt', 'rdfa'],
      help = "The format of the RDF document(s) which serve as the initial facts "+
             " for the RETE network. One of 'xml','n3','trix', 'nt', "+
             "or 'rdfa'.  The default is %default")
    op.add_option('--safety', 
                  default='none',
                  metavar='RULE_SAFETY',
                  choices = ['loose', 'strict','none'],
      help = "Determines how to handle RIF Core safety.  A value of 'loose' "+
             " means that unsafe rules will be ignored.  A value of 'strict' "+
             " will cause a syntax exception upon any unsafe rule.  A value of "+
             "'none' (the default) does nothing")    
    op.add_option('--pDSemantics', 
                  action='store_true',
                  default=False,
      help = 'Used with --dlp to add pD semantics ruleset for semantics not covered '+
      'by DLP but can be expressed in definite Datalog Logic Programming'+
      ' The default is %default')
    op.add_option('--stdin', 
                  action='store_true',
                  default=False,
      help = 'Parse STDIN as an RDF graph to contribute to the initial facts. The default is %default ')
    op.add_option('--ns', 
                  action='append',
                  default=[],
                  metavar="PREFIX=URI",
      help = 'Register a namespace binding (QName prefix to a base URI).  This '+
             'can be used more than once')
    op.add_option('--rules', 
                  default=[],
                  action='append',
                  metavar='PATH_OR_URI',
      help = 'The Notation 3 documents to use as rulesets for the RETE network'+
      '.  Can be specified more than once')
    op.add_option('-d', '--debug', action='store_true',default=False,
      help = 'Include debugging output')
    op.add_option('--strictness',
                  default='defaultBase',
                  metavar='DDL_STRICTNESS',
                  choices = ['loose', 
                             'defaultBase', 
                             'defaultDerived', 
                             'harsh'],
      help = 'Used with --why to specify whether to: *not* check if predicates are '+
      ' both derived and base (loose), if they are, mark as derived (defaultDerived) '+
      'or as base (defaultBase) predicates, else raise an exception (harsh)')    
    op.add_option('--firstAnswer',
                  default=False,
                  action='store_true',
      help = 'Used with --why to determine whether to fetch all answers or just '+
      'the first')            
    op.add_option('--edb',
                  default=[],
                  action='append',
                  metavar='EXTENSIONAL_DB_PREDICATE_QNAME',                  
      help = 'Used with --why/--strictness=defaultDerived to specify which clashing '+
      'predicate will be designated as a base predicate')
    op.add_option('--idb',
                  default=[],
                  action='append',
                  metavar='INTENSIONAL_DB_PREDICATE_QNAME',                  
      help = 'Used with --why/--strictness=defaultBase to specify which clashing '+
      'predicate will be designated as a derived predicate')                    
    op.add_option('--hybridPredicate',
                default=[],
                action='append',
                metavar='PREDICATE_QNAME',                  
    help = 'Used with --why to explicitely specify a hybrid predicate (in both '+
           ' IDB and EDB) ')
      
    op.add_option('--noMagic',
                  default=[],
                  action='append',
                  metavar='DB_PREDICATE_QNAME',                  
      help = 'Used with --why to specify that the predicate shouldnt have its '+
      'magic sets calculated')                    
    op.add_option('--filter', 
                  action='append',
                  default=[],
                  metavar='PATH_OR_URI',
      help = 'The Notation 3 documents to use as a filter (entailments do not particpate in network)')
    op.add_option('--ruleFacts', 
                  action='store_true',
                  default=False,
      help = "Determines whether or not to attempt to parse initial facts from "+
      "the rule graph.  The default is %default")
    op.add_option('--builtins', 
                  default=False,
                  metavar='PATH_TO_PYTHON_MODULE',
      help = "The path to a python module with function definitions (and a "+
      "dicitonary called ADDITIONAL_FILTERS) to use for builtins implementations")
    op.add_option('--dlp', 
                  action='store_true',
                  default=False,
      help = 'Use Description Logic Programming (DLP) to extract rules from OWL/RDF.  The default is %default')
    op.add_option('--sparqlEndpoint', 
                action='store_true',
                default=False,
    help = 'Indicates that the sole argument is the URI of a SPARQL endpoint to query')

    op.add_option('--ontology', 
                  action='append',
                  default=[],
                  metavar='PATH_OR_URI',
      help = 'The path to an OWL RDF/XML graph to use DLP to extract rules from '+
      '(other wise, fact graph(s) are used)  ')

    op.add_option('--ruleFormat',
        default='n3',
        dest='ruleFormat',
        metavar='RULE_FORMAT',
        choices = ['n3', 'rif'],
        help = "The format of the rules to parse ('n3', 'rif').  The default is %default")

    op.add_option('--ontologyFormat', 
                default='xml',
                dest='ontologyFormat',
                metavar='RDF_FORMAT',
                choices = ['xml', 'trix', 'n3', 'nt', 'rdfa'],
    help = "The format of the OWL RDF/XML graph specified via --ontology.  The default is %default")
   
    op.add_option('--builtinTemplates', 
                  default=None,
                  metavar='N3_DOC_PATH_OR_URI',
      help = 'The path to an N3 document associating SPARQL FILTER templates to '+
      'rule builtins')        
    op.add_option('--normalForm',
                  action='store_true',
                  default=False,                
      help = 'Whether or not to reduce DL axioms & LP rules to a normal form')        
    (options, facts) = op.parse_args()
    
    nsBinds = {'iw':'http://inferenceweb.stanford.edu/2004/07/iw.owl#'}
    for nsBind in options.ns:
        pref,nsUri = nsBind.split('=')
        nsBinds[pref]=nsUri
    
    namespace_manager = NamespaceManager(Graph())
    if options.sparqlEndpoint:
        factGraph = Graph(plugin.get('SPARQL',Store)(facts[0])) 
        options.hybrid = False
    else:
        factGraph = Graph() 
    ruleSet = Ruleset()

    for fileN in options.rules:
        if options.ruleFacts and not options.sparqlEndpoint:
            factGraph.parse(fileN,format='n3')
            print >>sys.stderr,"Parsing RDF facts from ", fileN
        if options.builtins:
            import imp
            userFuncs = imp.load_source('builtins', options.builtins)
            rs = HornFromN3(fileN,
                            additionalBuiltins=userFuncs.ADDITIONAL_FILTERS)
            nsBinds.update(rs.nsMapping)
        elif options.ruleFormat == 'rif':
            try:
                from FuXi.Horn.RIFCore import RIFCoreParser
                rif_parser = RIFCoreParser(
                    location=fileN,
                    debug=options.debug,
                    nsBindings=nsBinds)
                rs,facts = rif_parser.getRuleset()
            except ImportError, e:
                raise Exception(
                    "Missing 3rd party libraries for RIF processing: %s"%e
                )
        else:
            rs = HornFromN3(fileN)
            nsBinds.update(rs.nsMapping)
        ruleSet.formulae.extend(rs)
        #ruleGraph.parse(fileN,format='n3')

    ruleSet.nsMapping = nsBinds

    for prefix,uri in nsBinds.items():
        namespace_manager.bind(prefix, uri, override=False)
    closureDeltaGraph = Graph()
    closureDeltaGraph.namespace_manager = namespace_manager
    factGraph.namespace_manager = namespace_manager

    if not options.sparqlEndpoint:
        for fileN in facts:
            factGraph.parse(fileN,format=options.inputFormat)
            if options.imports:
                for owlImport in factGraph.objects(predicate=OWL_NS.imports):
                    factGraph.parse(owlImport)
                    print >>sys.stderr, "Parsed Semantic Web Graph.. ", owlImport
                
    if not options.sparqlEndpoint and facts:
        for pref,uri in factGraph.namespaces():
            nsBinds[pref]=uri
        
    if options.stdin:
        assert not options.sparqlEndpoint,"Cannot use --stdin with --sparqlEndpoint"
        factGraph.parse(sys.stdin,format=options.inputFormat)
        
    #Normalize namespace mappings
    #prune redundant, rdflib-allocated namespace prefix mappings
    newNsMgr = NamespaceManager(factGraph)
    from FuXi.Rete.Util import CollapseDictionary    
    for k,v in CollapseDictionary(dict([(k,v) 
                                    for k,v in factGraph.namespaces()])).items():
        newNsMgr.bind(k,v)
    factGraph.namespace_manager = newNsMgr
        
    if options.normalForm:
        NormalFormReduction(factGraph)

    if not options.sparqlEndpoint and options.naive:
        workingMemory = generateTokenSet(factGraph)
    if options.builtins:
        import imp
        userFuncs = imp.load_source('builtins', options.builtins)
        rule_store, rule_graph, network = SetupRuleStore(
                             makeNetwork=True,
                             additionalBuiltins=userFuncs.ADDITIONAL_FILTERS)
    else:
        rule_store, rule_graph, network = SetupRuleStore(makeNetwork=True)
    network.inferredFacts = closureDeltaGraph
    network.nsMap = nsBinds
    
    if options.dlp:
        from FuXi.DLP.DLNormalization import NormalFormReduction
        if options.ontology:
            ontGraph = Graph()
            for fileN in options.ontology:
                ontGraph.parse(fileN,format=options.ontologyFormat)
                for prefix,uri in ontGraph.namespaces():
                    nsBinds[prefix] = uri
                    namespace_manager.bind(prefix, uri, override=False)
                    if options.sparqlEndpoint:
                        factGraph.store.bind(prefix,uri)
        else:
            ontGraph=factGraph
        NormalFormReduction(ontGraph)
        dlp=network.setupDescriptionLogicProgramming(
                                 ontGraph,
                                 addPDSemantics=options.pDSemantics,
                                 constructNetwork=False,
                                 safety = safetyNameMap[options.safety])
        ruleSet.formulae.extend(dlp)
    if options.output == 'rif' and not options.why:
        for rule in ruleSet:
            print rule

    elif options.output == 'man-owl':
        cGraph = network.closureGraph(factGraph,readOnly=False)
        cGraph.namespace_manager = namespace_manager
        Individual.factoryGraph = cGraph
        if options.classes:
            mapping = dict(namespace_manager.namespaces())
            for c in options.classes:
                pref,uri=c.split(':')
                print Class(URIRef(mapping[pref]+uri)).__repr__(True)
        elif options.properties:
            mapping = dict(namespace_manager.namespaces())
            for p in options.properties:
                pref,uri=p.split(':')
                print Property(URIRef(mapping[pref]+uri))
        else:
            for p in AllProperties(cGraph):
                print p.identifier, first(p.label)
                print repr(p)
            for c in AllClasses(cGraph):
                if options.normalize:
                    if c.isPrimitive():
                        primAnc = [sc for sc in c.subClassOf if sc.isPrimitive()] 
                        if len(primAnc)>1:
                            warnings.warn("Branches of primitive skeleton taxonomy"+
                              " should form trees: %s has %s primitive parents: %s"%(
                             c.qname,len(primAnc),primAnc),UserWarning,1)
                        children = [desc for desc in c.subSumpteeIds()]
                        for child in children:
                            for otherChild in [o for o in children if o is not child]:
                                if not otherChild in [c.identifier
                                          for c in Class(child).disjointWith]:# and\
                                    warnings.warn("Primitive children (of %s) "%(c.qname) +
                                          "must be mutually disjoint: %s and %s"%(
                                      Class(child).qname,
                                      Class(otherChild).qname),UserWarning,1)
#                if not isinstance(c.identifier,BNode):
                print c.__repr__(True)
                    
    if not options.why:
        #Niave construction of graph
        for rule in ruleSet:
            network.buildNetworkFromClause(rule)

    magicSeeds=[]
    if options.why:
        builtinTemplateGraph = Graph()
        if options.builtinTemplates:
            builtinTemplateGraph = Graph().parse(options.builtinTemplates,
                                                format='n3')
        factGraph.templateMap = \
            dict([(pred,template)
                      for pred,_ignore,template in 
                            builtinTemplateGraph.triples(
                                (None,
                                 TEMPLATES.filterTemplate,
                                 None))])
        goals=[]
        query = ParseSPARQL(options.why) 
        network.nsMap['pml'] = PML
        network.nsMap['gmp'] = GMP_NS
        network.nsMap['owl'] = OWL_NS        
        nsBinds.update(network.nsMap)
        network.nsMap = nsBinds
        if not query.prolog:
                query.prolog = Prolog(None, [])
                query.prolog.prefixBindings.update(nsBinds)
        else:
            for prefix, nsInst in nsBinds.items():
                if prefix not in query.prolog.prefixBindings:
                    query.prolog.prefixBindings[prefix] = nsInst
        goals.extend([(s,p,o) for s,p,o,c in ReduceGraphPattern(
                                    query.query.whereClause.parsedGraphPattern,
                                    query.prolog).patterns])
        dPreds=[]# p for s,p,o in goals ]
        magicRuleNo = 0
        bottomUpDerivedPreds = []
        topDownDerivedPreds  = []
        defaultBasePreds     = []
        defaultDerivedPreds  = set()
        hybridPredicates     = []
        mapping = dict(newNsMgr.namespaces())
        for edb in options.edb: 
            pref,uri=edb.split(':')
            defaultBasePreds.append(URIRef(mapping[pref]+uri))
        noMagic = []
        for pred in options.noMagic:
            pref,uri=pred.split(':')
            noMagic.append(URIRef(mapping[pref]+uri))
        if options.ddlGraph:
            ddlGraph = Graph().parse(options.ddlGraph,format='n3')
            # @TODO: should also get hybrid predicates from DDL graph
            defaultDerivedPreds=IdentifyDerivedPredicates(
                                    ddlGraph,
                                    Graph(),
                                    ruleSet)
        else:
            for idb in options.idb: 
                pref,uri=idb.split(':')
                defaultDerivedPreds.add(URIRef(mapping[pref]+uri))
            defaultDerivedPreds.update(set([p == RDF.type and o or p for s,p,o in goals]))
            for hybrid in options.hybridPredicate:
                pref,uri=hybrid.split(':')
                hybridPredicates.append(URIRef(mapping[pref]+uri))
        
        topDownDPreds = defaultDerivedPreds
        if options.builtinTemplates:
            builtinTemplateGraph = Graph().parse(options.builtinTemplates,
                                                format='n3')
            builtinDict = dict([(pred,template)
                          for pred,_ignore,template in
                                builtinTemplateGraph.triples(
                                    (None,
                                     TEMPLATES.filterTemplate,
                                     None))])
        else:
            builtinDict = None
        topDownStore=TopDownSPARQLEntailingStore(
                        factGraph.store,
                        factGraph,
                        idb=ruleSet,
                        DEBUG=options.debug,
                        derivedPredicates = topDownDPreds,
                        templateMap = builtinDict,
                        nsBindings=network.nsMap,
                        identifyHybridPredicates =
                        options.hybrid,
                        hybridPredicates = hybridPredicates)
        targetGraph = Graph(topDownStore)
        for pref,nsUri in network.nsMap.items():
            targetGraph.bind(pref,nsUri)
        start = time.time()
        # queryLiteral = EDBQuery([BuildUnitermFromTuple(goal) for goal in goals],
        #                         targetGraph)
        # query = queryLiteral.asSPARQL()
        # print >>sys.stderr, "Goal to solve ", query
        sTime = time.time() - start
        result = targetGraph.query(options.why,initNs=network.nsMap)
        if result.askAnswer:
            sTime = time.time() - start
            if sTime > 1:
                sTimeStr = "%s seconds"%sTime
            else:
                sTime = sTime * 1000
                sTimeStr = "%s milli seconds"%sTime
            print >>sys.stderr,\
"Time to reach answer ground goal answer of %s: %s"%(result.askAnswer[0],sTimeStr)
        else:
            for rt in result:
                sTime = time.time() - start
                if sTime > 1:
                    sTimeStr = "%s seconds"%sTime
                else:
                    sTime = sTime * 1000
                    sTimeStr = "%s milli seconds"%sTime
                if options.firstAnswer:
                    break
                print >>sys.stderr,\
    "Time to reach answer %s via top-down SPARQL sip strategy: %s"%(rt,sTimeStr)
        if options.output == 'conflict':
            for _network,_goal in topDownStore.queryNetworks:
                print >>sys.stderr, _network, _goal
                _network.reportConflictSet(options.debug)
            for query in topDownStore.edbQueries:
                print >>sys.stderr,query.asSPARQL()

    for fileN in options.filter:
        for rule in HornFromN3(fileN):
            network.buildFilterNetworkFromClause(rule)

    if options.filter:
        print >>sys.stderr,"Applying filter to entailed facts"
        network.inferredFacts = network.filteredFacts

    if options.naive:
        start = time.time()
        network.feedFactsToAdd(workingMemory)
        sTime = time.time() - start
        if sTime > 1:
            sTimeStr = "%s seconds"%sTime
        else:
            sTime = sTime * 1000
            sTimeStr = "%s milli seconds"%sTime
        print >>sys.stderr,"Time to calculate closure on working memory: ",sTimeStr
        print >>sys.stderr, network
        if options.output == 'conflict':
            network.reportConflictSet()
        elif options.closure and options.output in RDF_SERIALIZATION_FORMATS:
            cGraph = network.closureGraph(factGraph)
            cGraph.namespace_manager = namespace_manager
            print cGraph.serialize(destination=None,
                                   format=options.output,
                                   base=None)
        elif options.output and options.output in RDF_SERIALIZATION_FORMATS:
            print network.inferredFacts.serialize(destination=None,
                                                  format=options.output,
                                                  base=None)
if __name__ == '__main__':
    from hotshot import Profile, stats
#    import pycallgraph
#    pycallgraph.start_trace()
#    main()
#    pycallgraph.make_dot_graph('FuXi-timing.png')
#    sys.exit(1)
    p = Profile('fuxi.profile')
    p.runcall(main)
    p.close()    
    s = stats.load('fuxi.profile')
    s.strip_dirs()
    s.sort_stats('time','cumulative','pcalls')
    s.print_stats(.05)
    s.print_callers(.01)
    s.print_callees(.01)            