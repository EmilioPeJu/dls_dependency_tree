#!/bin/env python2.4

author = "Tom Cobb"
usage = """%prog [options] <module_path>

Creates the dependency tree from <module_path>/configure/RELEASE and flattens 
it, returning a colon separated list of paths matching glob "/data".

E.g. If a module with path PATH is listed in <module_path>/configure/RELEASE, 
and it has a directory PATH/data, then PATH/data will appear in the output list 
of paths. If <glob>="*App/opi/edl" and PATH/moduleApp/opi/edl exists then it
will appear in the output list of paths"""

import os, sys, shutil, re, glob
from optparse import OptionParser

class dependency_tree:
    """A class for parsing configure/RELEASE for module names and versions"""
    ## Macro names that we don't want to appear in our release tree
    ignore_list = ["TEMPLATE_TOP","EPICS_BASE","PYTHONPATH","EPICS_RELEASE",\
                   "EPICS_EXTENSIONS"]
    ## Module names with no configure/RELEASE file
    no_release = ["superTop"]       
    
    def __init__(self,parent=None,module_path=None,includes=True,warnings=True):
        """Initialise the object
        
        Arguments:
        - parent:      The parent dependency_tree or None
        - module_path: The path to the module root, or the configure/RELEASE 
                       file. If this is not specified then it doesn't parse
                       any configure/RELEASE file until process_module is called
        - includes:    Whether to process include and -include statements in
                       configure/RELEASE files
        - warnings:    Whether to print warnings or not
        """
        # setup variables to defaults
        self.module_path = module_path
        self.parent=parent        
        self.includes = includes
        self.warnings = warnings
        self._release = None
        self.reinit()
    
    def reinit(self):
        """Re-initialise the object."""
        from dls_environment import environment
        ## dls.environment object for getting paths and release order
        self.e = environment()
        ## list of child dependency_tree leaves of this modules
        self.leaves=[]
        ## path to module root (like /dls_sw/work/R3.14.8.2/support/motor)
        self.path = ""
        ## name of the module (like motor or sscan)
        self.name = ""
        ## version of module (like 6-3dls1 or work or local)
        self.version = ""
        ## dict of fully substituted macros 
        ## (like macros["SUPPORT"]="/dls_sw/prod/R3.14.8.2/support")
        self.macros={}
        ## stored lines of the RELEASE file. Updated as changes are written
        self.lines = []
        if self.module_path:
            # import a configure RELEASE
            self.process_module(self.module_path)        
                    
    def copy(self):
        """Return a copy of this dependency_tree object"""
        new_tree = dependency_tree(self.parent,warnings=self.warnings)
        new_tree.e = self.e.copy()
        new_tree.path = self.path
        new_tree.name = self.name
        new_tree.version = self.version
        new_tree.macros = self.macros.copy()
        new_tree.lines = self.lines[:]
        for leaf in self.leaves:
            new_leaf = leaf.copy()
            new_leaf.parent = new_tree
            new_tree.leaves.append(new_leaf)
        return new_tree
                
    def __repr__(self):
        """Override the repr method so that "print dependency_tree" gives a 
        useful output"""
        return "<dependency_tree - "+self.name+": "+self.version+">"

    def __eq__(self,tree):
        """Override the == method so it checks for name, version, and equality
        of leaves"""
        output = self.name==tree.name and self.version==tree.version and \
                 len(self.leaves)==len(tree.leaves)
        if not output:
            return output
        for i in range(len(self.leaves)):
            output = output and self.leaves[i]==tree.leaves[i]
        return output

    def init_version(self):
        """Initialise self.name and self.version from self.path using the site
        environment settings"""
        self.name, self.version = self.e.classifyPath(self.path)

    def __possible_paths(self):
        """Return a list of all possible module paths for self. These are listed
        in ascending order."""
        if "ioc" in self.path:
            prefix = self.e.prodArea("ioc")
        else:
            prefix = self.e.prodArea("support")
        prefix = os.path.join(prefix,self.name)
        if os.path.isdir(prefix):
            paths = [os.path.join(prefix,x) for x in os.listdir(prefix)]
        else:
            paths = []
        if self.path not in paths:
            paths = [self.path]+paths
        # return paths listed in ascending order
        return self.e.sortReleases(paths)


    def __substitute_macros(self,modules):
        """Substitute macros in modules according to self.macros. modules is a 
        dict of the format modules[name]=path. This function returns the dict
        with all macro values substituted."""
        retries = 5
        while retries>0:
            unsubbed_macros = []
            macro_re = re.compile(r"\$\(([^\)]+)\)")
            for macro in modules.keys():
                for find in macro_re.findall(modules[macro]):
                    # find all unsubstituted macros, and replace them with their
                    # substitutions
                    if find in self.macros.keys():
                        modules[macro]=modules[macro].replace("$("+find+")",\
                                                              self.macros[find])
                    else:
                        modules[macro]=modules[macro].replace("$("+find+")","")
            retries-=1
        return modules

    def __process_line(self,line):
        """Process a line of configure/RELEASE after comments have been 
        stripped out"""
        # check the line defines a macro
        if "=" in line:
            list = [x.strip() for x in line.split("=")]
            # try and find epics base in the line
            match = self.e.epics_ver_re.search(list[1])
            if list[0] == "EPICS_BASE" and match:
                # if epics version is defined, set it in the environment
                self.e.setEpics( match.group() )
            else:
                # otherwise, define it in the module dictionary
                assert not self.modules.has_key(list[0]), "Macro: "+list[0]+\
                    " defined multiple times in "+self.path
                self.modules[list[0]]=list[1]
                self.module_order.append(list[0])        

    def process_module(self,module_path):
        """Process the configure/RELEASE file and populate the tree from it.
        module_path is the path to configure/RELEASE """
        # define some initial values
        ## Very similar to \ref macros, but only modules are listed
        self.modules = {"TOP":"."}
        ## The order that the modules were declared in RELEASE
        self.module_order = []

        # list of definitions that are not support modules
        ignore_list = self.ignore_list
        
        # list of module_names without a RELEASE file
        no_release = self.no_release

        # set the path
        self.path=os.path.abspath(os.path.expanduser(module_path.rstrip("/\n\r")))

        # if the path ends in RELEASE, then use this as the RELEASE file
        # if RELEASE does not exist, make this a dummy module
        if self.path.endswith("RELEASE"):
            self._release = self.path
            self.path = '/'.join(self.path.split("/")[:-2])
        if not os.path.isfile(self.release()):
            self.init_version()
            if self.name in no_release and os.path.isdir(self.path):
                # this module has no release file, but is to be treated as valid
                return
            else:
                self.version="invalid"
                if self.warnings:
                    print >> sys.stderr, "***Warning: can't find module: "+\
                        self.name+" with RELEASE file: "+self.release()
                return
            
        # read in RELEASE
        input = open(self.release(),"r")
        self.lines = input.readlines()
        input.close()

        # store current working directory then go to module base
        cwd = os.getcwd()
        os.chdir(self.path)

        # for each line in the RELEASE file, populate the modules dictionary if 
        # it defines a support module
        for line in self.lines:
            # strip comments
            line = line.split("#")[0]
            # check if the line contains "include" but not "-include". This will
            # be a reference to a RELEASE file elsewhere in the file system
            if "include" in line[:8]:
                if self.includes:
                    fname = line.split(" ")[1].rstrip()
                    for module in self.modules:
                        fname = fname.replace("$("+module+")",self.modules[module])                  
                    try:
                        for line in open(fname,"r").readlines():
                            self.__process_line(line)
                    except IOError:
                        pass
            else:
                self.__process_line(line)
        
        # we now have our RELEASE file as a macro dict, so store this
        self.macros = self.modules
        
        # then set the name and version of the tree
        self.init_version()

        # do some error checking
        if self.parent and self.name==self.parent.name:
            # module refers to itself, probably for an example app. ignore it"
            return

        # now try and substitute macros
        self.__substitute_macros(self.modules)

        # remove any modules we know to be wrong and make trees from the rest of
        # them
        for module in self.module_order:
            if module=="TOP" or self.modules[module]==".":
                # ignore TOP and any module that refers to itself explicitly
                pass
            elif self.modules[module].upper() in ["YES","NO","TRUE","FALSE"]:
                # ignore flags
                pass
            elif "python" in self.modules[module]:
                # ignore python as it has its own build system
                pass
            elif self.modules[module] in [self.e.devArea("support"),\
                                          self.e.devArea("ioc")]:
                # ignore macros defining the development areas
                pass
            elif self.modules[module] in [self.e.prodArea("support"),\
                                          self.e.prodArea("ioc")]:
                # ignore macros defining the production areas
                pass
                        
            elif module not in ignore_list:
                # module is probably valid
                # so make a tree from it and add it to leaves
                new_leaf = dependency_tree(parent=self,\
                    module_path=self.modules[module],warnings=self.warnings)
                self.leaves.append(new_leaf)
                
        # go back to initial place and return the values
        os.chdir(cwd)


    def flatten(self,include_self=True,remove_dups=True):
        """Return a flattened list of leaves. If include_self, append self to 
        the list. Then flatten each leaf in turn and append it to the list. 
        If remove_dups then get rid of duplicate leaves. Finally return this
        list"""
        output = []
        for leaf in self.leaves:
            flattened_list = leaf.flatten()
            for leaf in flattened_list:
                in_list = False
                for path in [x.path for x in output]:
                    if leaf.path == path:
                        in_list = True
                if not remove_dups or not in_list:
                    output.append(leaf)
        if include_self:
            output.append(self)
        return output

    def paths(self,globs=["/data"],include_name=False):
        """For each glob in globs, return the list of paths matching
        leaf.path+glob for each leaf in self.flatten(). A glob is something like
        '/db' or '/*App/opi/edl/*.edl'. If include_name, then return a tuple of
        the list of module names with the list of module paths, otherwise
        just return the list of module paths"""
        poutput = []
        noutput = []
        leaves = self.flatten()
        for leaf in leaves:
            for g in globs:
                gg = glob.glob(leaf.path+g)
                poutput.extend(gg)
                noutput.extend([leaf.name]*len(gg))
        if include_name:
            return (noutput,poutput)
        else:
            return poutput


    def clashes(self,print_warnings=True):
        """Return a dict of all clashes occurring in the tree. This dict has the
        format clashes[name] = [leaves]. The leaves associated with each name
        have leaf.name == name, and all have different leaf.version numbers. If
        print_warnings, then warn if clashes exist."""
        leaves = self.flatten(remove_dups=False)
        # clashes[name] = [leaves]
        clashes = {}
        # sort the flattened leaves by module name
        for leaf in leaves:
            if clashes.has_key(leaf.name):
                clashes[leaf.name]+=[leaf]
            else:
                clashes[leaf.name]=[leaf]
        # discard modules that are not causing a problem
        for key in clashes.keys():
            # check version is identical to first in the list
            compare = [ clashes[key][0].version == x.version for x in \
                        clashes[key] ]
            if min(compare) == 1:
                del(clashes[key])
            else:
                if print_warnings:
                    print >> sys.stderr, "*** Warning: releases do not form a consistent set:"
                for leaf in clashes[key]:
                    if print_warnings:
                        print >> sys.stderr, leaf.parent.name + ": " + leaf.parent.version + " defines " + leaf.name + " as " + leaf.path
        # now sort clashes by version, lowest first
        for name in clashes.keys():
            modules = [ (m.path,m) for m in clashes[name] ]
            new_list = [ x[1] for x in self.e.sortReleases(modules) ]
            clashes[name] = new_list
        return clashes
                    
                    
    def updates(self):
        """Return all possible paths for self that are considered updates"""
        paths = self.__possible_paths()
        return paths[paths.index(self.path):]


    def print_tree(self,spaces=0):
        """Print an ascii art text representation of self"""
        print " |"*spaces+"-"+self.name+": "+self.version
        for leaf in self.leaves:
            leaf.print_tree(spaces+1)

    def release(self):
        """Return the path to the RELEASE file"""
        # search for a suitable release number in the path
        if self._release is not None:
            return self._release
        ver = self.e.epics_ver_re.search(self.path)
        if ver and ver.group()<"R3.14":
            self.e.setEpics(ver.group())
        # if this cannot be found, use the default value
        if self.e.epicsVer()<"R3.14":       
            release = os.path.join(self.path,"config","RELEASE")
        else:
            release = os.path.join(self.path,"configure","RELEASE")
        return release
        

    def replace_leaf(self,leaf,new_leaf):
        """Replace leaf with new_leaf. Update self.lines accordingly"""
        assert leaf in self.leaves, \
            "Module not listed in this tree, can't replace it: "+leaf.path
        self.leaves[self.leaves.index(leaf)]=new_leaf
        # expand home dir
        home = os.path.expanduser("~")
        if leaf.path.startswith(home):
            leaf_path = "~" + leaf.path[len(home):]
        else:
            leaf_path = leaf.path
        if new_leaf.path.startswith(home):
            new_leaf_path = "~" + new_leaf.path[len(home):]
        else:
            new_leaf_path = new_leaf.path          
        # find the macro so that its substitution = leaf.path            
        for macro in self.macros.keys():
            if self.macros[macro]==leaf_path:
                break
        # find the line in RELEASE that refers to it
        for i,line in enumerate(self.lines):
            line = line.split("#")[0]
            if "=" in line:
                list = [x.strip() for x in line.split("=")]
                if macro == list[0]:
                    break
        # replace macros in that line
        dict = {}
        dict[list[0]] = list[1]
        new_line = line.replace(list[1],self.__substitute_macros(dict)[list[0]])          
        # now replace the old leaf path for the new leaf path
        if leaf_path not in new_line:
            print >> sys.stderr, "Module path: "+leaf_path+\
                                 " should be in this line: "+new_line
            return
        new_line = new_line.replace(leaf_path,new_leaf_path)
        # now put macros back in and set the new line in RELEASE
        self.lines[i]=self.replace_macros(new_line,[macro])
        self.macros[macro]=new_leaf_path        

    def replace_macros(self,line,exclude_list=[]):
        rev_macros = {}
        for key in set(self.macros)-set(["TOP"]+exclude_list):
            rev_macros[self.macros[key]]="$("+key+")"
        sub_list = reversed(sorted(rev_macros.keys()))
        for sub in sub_list:
            line = line.replace(sub,rev_macros[sub])
        return line
        
def cl_dependency_tree():
    parser = OptionParser(usage)
    glob="/data"
    separator=":"
    parser.add_option("-g", "--glob", dest="glob", metavar="GLOB", 
                      help="Set suffix for paths. Default is '"+glob+"'")
    parser.add_option("-s", "--separator", dest="separator", metavar="CHAR", 
                      help="Set the separator for the list. Default is '%s'"%\
                      separator)
    parser.add_option("-n", "--newline", action="store_true", dest="newline", 
                      help="Set the separator for the list to be the newline "\
                      "character")
    (options, args) = parser.parse_args()
    if len(args)!=1:
        parser.error("Incorrect number of args - run program with -h for help")
    if options.glob:
        glob = [options.glob]
    else:
        glob= [glob]
    if options.separator:
        separator = options.separator
    if options.newline:
        separator = "\n"
    tree = dependency_tree(None,module_path=args[0])
    paths = tree.paths(glob)
    print separator.join(paths)
cl_dependency_tree.__doc__=usage

if __name__=="__main__":
    from pkg_resources import require
    require("dls.environment==1.0")
    cl_dependency_tree()