procedure DumpBO(BO: Variant; Tag: String);
var i: Integer; S: Variant;
begin
  L(Tag + ' count=' + IntToStr(BO.PointCount));
  for i := 0 to BO.PointCount - 1 do
  begin
    S := BO.Segments[i];
    L('  ' + IntToStr(i) + ' kind=' + IntToStr(S.Kind) + ' v=' + FloatToStr(CoordToMMs(S.vx)) + ',' + FloatToStr(CoordToMMs(S.vy)) + ' c=' + FloatToStr(CoordToMMs(S.cx)) + ',' + FloatToStr(CoordToMMs(S.cy)) + ' r=' + FloatToStr(CoordToMMs(S.Radius)) + ' a=' + FloatToStr(S.Angle1) + '..' + FloatToStr(S.Angle2));
  end;
end;

function Seg(K: Integer; VX, VY, CX, CY, R, A1, A2: Double): Variant;
begin
  Result := TPolySegment;
  Result.Kind := K;
  Result.vx := MMsToCoord(VX); Result.vy := MMsToCoord(VY);
  Result.cx := MMsToCoord(CX); Result.cy := MMsToCoord(CY);
  Result.Radius := MMsToCoord(R); Result.Angle1 := A1; Result.Angle2 := A2;
end;

procedure Body;
var
  Doc, Board, BO, P, Net: Variant;
begin
  Doc := CreateNewDocumentFromDocumentKind('PCB');
  Board := PCBServer.GetCurrentPCBBoard;
  BO := Board.BoardOutline;
  DumpBO(BO, 'default');
  BO.BeginModify;
  BO.PointCount := 4;
  BO.Segments[0] := Seg(ePolySegmentLine, 100, 100, 0, 0, 0, 0, 0);
  BO.Segments[1] := Seg(ePolySegmentArc, 150, 100, 150, 115, 15, 270, 90);
  BO.Segments[2] := Seg(ePolySegmentLine, 150, 130, 0, 0, 0, 0, 0);
  BO.Segments[3] := Seg(ePolySegmentLine, 100, 130, 0, 0, 0, 0, 0);
  BO.EndModify;
  DumpBO(BO, 'after set');
  BO.Invalidate; BO.Rebuild; BO.Validate;
  DumpBO(BO, 'after rebuild');
  Net := PCBServer.PCBObjectFactory(eNetObject, eNoDimension, eCreate_Default);
  Net.Name := 'GND';
  Board.AddPCBObject(Net);
  P := PCBServer.PCBObjectFactory(ePolyObject, eNoDimension, eCreate_Default);
  P.Layer := eTopLayer;
  P.PolyHatchStyle := ePolySolid;
  P.PointCount := 4;
  P.Segments[0] := Seg(ePolySegmentLine, 105, 105, 0, 0, 0, 0, 0);
  P.Segments[1] := Seg(ePolySegmentLine, 140, 105, 0, 0, 0, 0, 0);
  P.Segments[2] := Seg(ePolySegmentLine, 140, 125, 0, 0, 0, 0, 0);
  P.Segments[3] := Seg(ePolySegmentLine, 105, 125, 0, 0, 0, 0, 0);
  P.Segments[4] := Seg(ePolySegmentLine, 105, 105, 0, 0, 0, 0, 0);
  P.Net := Net;
  P.PourOver := ePolygonPourOver_SameNet;
  P.RemoveDead := True;
  Board.AddPCBObject(P);
  P.SetState_CopperPourInvalid;
  P.Rebuild;
  P.SetState_CopperPourValid;
  L('poly poured=' + BoolToStr(P.Poured, True) + ' prims? ');
  ResetParameters;
  AddStringParameter('Action', 'All');
  RunProcess('PCB:Zoom');
  Doc.SetFileName('%ROOT%\devtools\_probe_run\p10.PcbDoc');
  Doc.DoFileSave('');
end;
