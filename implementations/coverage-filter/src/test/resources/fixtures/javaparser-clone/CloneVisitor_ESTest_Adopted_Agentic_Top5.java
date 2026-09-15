/*
 * Copyright (C) 2007-2010 Júlio Vilmar Gesser.
 * Copyright (C) 2011, 2013-2024 The JavaParser Team.
 *
 * This file is part of JavaParser.
 *
 * JavaParser can be used either under the terms of
 * a) the GNU Lesser General Public License as published by
 *     the Free Software Foundation, either version 3 of the License, or
 *     (at your option) any later version.
 * b) the terms of the Apache License
 *
 * You should have received a copy of both licenses in LICENCE.LGPL and
 * LICENCE.APACHE. Please refer to those files for details.
 *
 * JavaParser is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
 * GNU Lesser General Public License for more details.
 */

package com.github.javaparser.ast.visitor;

import static com.github.javaparser.StaticJavaParser.parseType;
import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertNotSame;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertTrue;

import com.github.javaparser.JavaToken;
import com.github.javaparser.TokenRange;
import com.github.javaparser.ast.ArrayCreationLevel;
import com.github.javaparser.ast.CompilationUnit;
import com.github.javaparser.ast.ImportDeclaration;
import com.github.javaparser.ast.Modifier;
import com.github.javaparser.ast.Node;
import com.github.javaparser.ast.NodeList;
import com.github.javaparser.ast.body.*;
import com.github.javaparser.ast.comments.BlockComment;
import com.github.javaparser.ast.comments.LineComment;
import com.github.javaparser.ast.expr.*;
import com.github.javaparser.ast.modules.ModuleRequiresDirective;
import com.github.javaparser.ast.modules.ModuleUsesDirective;
import com.github.javaparser.ast.stmt.*;
import com.github.javaparser.ast.type.*;
import java.util.Iterator;
import java.util.LinkedList;
import java.util.Optional;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

class CloneVisitor_ESTest_Adopted_Agentic_Top5 {
    CompilationUnit cu;

    @BeforeEach
    void setUp() {
        cu = new CompilationUnit();
    }

    @AfterEach
    void teardown() {
        cu = null;
    }

    @Test
    void visitForStmt_withBlockCommentListContext_returnsEqualButNotSame() {
        CloneVisitor visitor = new CloneVisitor();
        ForStmt forStmt = new ForStmt();
        BlockComment[] commentArray = new BlockComment[0];
        NodeList<BlockComment> comments = new NodeList<>(commentArray);
        Visitable cloned = visitor.visit(forStmt, comments);
        assertNotSame(cloned, forStmt);
        assertTrue(cloned.equals(forStmt));
    }

    @Test
    void visitForEachStmt_withObject_returnsClonedNode() {
        CloneVisitor visitor = new CloneVisitor();
        ForEachStmt forEach = new ForEachStmt();
        Object context = new Object();
        Visitable cloned = visitor.visit(forEach, context);
        assertNotSame(cloned, forEach);
    }

    @Test
    void visitArrayCreationExpr_withObject_returnsEqualButNotSame() {
        CloneVisitor visitor = new CloneVisitor();
        UnknownType unknown = new UnknownType();
        Object context = new Object();
        NodeList<ArrayCreationLevel> levels = new NodeList<>();
        ArrayInitializerExpr initializer = new ArrayInitializerExpr();
        ArrayCreationExpr arrayCreation = new ArrayCreationExpr(unknown, levels, initializer);
        Visitable cloned = visitor.visit(arrayCreation, context);
        assertTrue(cloned.equals(arrayCreation));
        assertNotSame(cloned, arrayCreation);
    }

    @Test
    void visitConditionalExpr_withObject_returnsEqualButNotSame() {
        CloneVisitor visitor = new CloneVisitor();
        ConditionalExpr conditional = new ConditionalExpr();
        Object context = new Object();
        Visitable cloned = visitor.visit(conditional, context);
        assertNotSame(cloned, conditional);
        assertTrue(cloned.equals(conditional));
    }

    @Test
    void visitInstanceOfExpr_withBlockComment_returnsEqualButNotSame() {
        CloneVisitor visitor = new CloneVisitor();
        InstanceOfExpr instanceOfExpr = new InstanceOfExpr();
        BlockComment contextComment = new BlockComment();
        Visitable cloned = visitor.visit(instanceOfExpr, contextComment);
        assertTrue(cloned.equals(instanceOfExpr));
        assertNotSame(cloned, instanceOfExpr);
    }
}